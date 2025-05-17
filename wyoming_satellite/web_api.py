"""Web API for Wyoming satellite."""

import asyncio
import logging
import time
from typing import Optional, Dict, Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from wyoming.satellite import RunSatellite
from wyoming.wake import Detection

# These imports are dynamically checked later in the code
# and safely imported if the modules are available
import_errors = []  # Collects missing modules for better error messages
try:
    import uvicorn
except ImportError as e:
    import_errors.append(f"uvicorn: {str(e)}")

_LOGGER = logging.getLogger(__name__)

# API Models
class WakeWordTriggerRequest(BaseModel):
    """Request to trigger wake word detection."""
    
    wake_word_name: Optional[str] = None
    pipeline: Optional[str] = None


app = FastAPI(title="Wyoming Satellite API", version="1.0.0")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global variables
satellite = None
last_state_change = time.time()  # Track when the state last changed
current_state = "idle"           # Current state of the satellite

@app.post("/api/trigger-wake")
async def trigger_wake_word(request: WakeWordTriggerRequest):
    """Triggers wake word detection."""
    if not satellite:
        raise HTTPException(status_code=500, detail="Satellite not initialized")
    
    try:
        # Create a Detection event with optional name
        wake_word_name = request.wake_word_name if request.wake_word_name else "web_trigger"
        
        # The Wyoming library does not support a pipeline parameter in Detection
        detection = Detection(
            name=wake_word_name,
            timestamp=0  # Ignored by the Satellite logic
        )
        
        # Store pipeline info in context log messages
        if request.pipeline:
            _LOGGER.info(f"Requested pipeline: {request.pipeline} (not directly supported by Detection event)")
            # This might be supported in newer versions, but we'll ignore it for now
        
        # Simulate wake word detection
        if isinstance(satellite, type) or not hasattr(satellite, "event_from_wake"):
            # Fallback for AlwaysStreamingSatellite and VadStreamingSatellite
            if not satellite.is_streaming:
                # Start streaming if not active
                run_satellite_event = RunSatellite().event()
                await satellite.event_from_server(run_satellite_event)
            return {"status": "success", "message": "Voice recognition started"}
        else:
            # For WakeStreamingSatellite
            await satellite.event_from_wake(detection.event())
            return {"status": "success", "message": "Wake word detected"}
            
    except Exception as e:
        _LOGGER.exception("Error triggering wake word")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/cancel")
async def cancel_pipeline():
    """Cancels the current pipeline."""
    if not satellite:
        raise HTTPException(status_code=500, detail="Satellite not initialized")
    
    try:
        # Check if streaming is active
        if hasattr(satellite, "is_streaming") and satellite.is_streaming:
            # Different satellite types have different methods to stop streaming
            # We use a generic approach:
            
            # 1. First try the direct method, if available
            if hasattr(satellite, "stop_streaming") and callable(getattr(satellite, "stop_streaming")):
                await satellite.stop_streaming()
                return {"status": "success", "message": "Pipeline cancelled"}
                
            # 2. Set the is_streaming flag directly back to False
            satellite.is_streaming = False
            
            # 3. Send an AudioStop event and an Error event, if possible
            from wyoming.audio import AudioStop
            from wyoming.error import Error
            if hasattr(satellite, "event_to_server") and callable(getattr(satellite, "event_to_server")):
                try:
                    # End the audio stream
                    await satellite.event_to_server(AudioStop().event())
                    
                    # Send an error to completely cancel the pipeline
                    error_event = Error(message="Pipeline cancelled by user").event()
                    await satellite.event_to_server(error_event)
                    _LOGGER.info("Sent pipeline cancellation signal to server")
                except Exception as e:
                    _LOGGER.warning(f"Failed to send stop events: {e}")
                    
            # 4. Trigger streaming_stop event
            if hasattr(satellite, "trigger_streaming_stop") and callable(getattr(satellite, "trigger_streaming_stop")):
                await satellite.trigger_streaming_stop()
                
            return {"status": "success", "message": "Pipeline cancelled"}
        else:
            return {"status": "info", "message": "No active pipeline"}
            
    except Exception as e:
        _LOGGER.exception("Error canceling pipeline")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/status")
async def get_status():
    """Returns the current status of the satellite."""
    global current_state, last_state_change
    
    if not satellite:
        raise HTTPException(status_code=503, detail="Satellite not initialized")
    
    # Determine current state based on satellite attributes
    is_active = False
    
    if hasattr(satellite, "is_streaming") and satellite.is_streaming:
        current_state = "streaming"
        is_active = True
    else:
        # Check if satellite is in wake word detection mode
        has_wake_word = hasattr(satellite, "_wake_client") and satellite._wake_client is not None
        if has_wake_word:
            current_state = "listening"
            is_active = True
        else:
            current_state = "idle"
    
    # Return status information
    return {
        "state": current_state,
        "is_active": is_active,
        "timestamp": int(time.time())
    }


@app.post("/api/reconnect")
async def reconnect():
    """Reconnect to Home Assistant."""
    if not satellite:
        raise HTTPException(status_code=500, detail="Satellite not initialized")
    
    try:
        # Reset server connection
        server_id = satellite.server_id
        writer = satellite._writer
        
        # Disconnect
        await satellite.clear_server()
        
        # Wait a moment
        await asyncio.sleep(1)
        
        # Reestablish connection if possible
        if server_id and writer:
            await satellite.set_server(server_id, writer)
            return {"status": "success", "message": "Connection reestablished"}
        else:
            return {"status": "warning", "message": "No active connection to reestablish"}
            
    except Exception as e:
        _LOGGER.exception("Error reconnecting")
        raise HTTPException(status_code=500, detail=str(e))


async def start_web_server(api_uri: str, satellite_instance):
    """Starts the web server with the API."""
    global satellite
    satellite = satellite_instance
    
    # Check if the required dependencies are installed
    if import_errors:
        error_message = "\n".join(import_errors)
        _LOGGER.error(f"Cannot start web API - missing dependencies:\n{error_message}")
        _LOGGER.error("Install required packages with: pip install fastapi uvicorn")
        return
    
    # Parse URI (format: http://host:port)
    try:
        from urllib.parse import urlparse
        parsed_uri = urlparse(api_uri)
        host = parsed_uri.hostname or "127.0.0.1"
        port = parsed_uri.port or 8080
        
        _LOGGER.info(f"Attempting to start web API server on {host}:{port}")
        
        # First check if the port is available
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = sock.connect_ex((host, port))
        sock.close()
        
        if result == 0:  # Port is already in use
            _LOGGER.error(f"Cannot start web API: Port {port} is already in use")
            return
            
        # Configure and start server with better error handling
        config = uvicorn.Config(app, host=host, port=port, log_level="error")
        server = uvicorn.Server(config)
        
        # Actually start serving
        _LOGGER.info(f"Starting web API server on {host}:{port}")
        await server.serve()
        _LOGGER.info(f"Web API server stopped")
        
    except Exception as e:
        _LOGGER.error(f"Failed to start web API server: {str(e)}")
        _LOGGER.exception(e)
