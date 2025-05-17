"""Web API for Wyoming satellite."""

import asyncio
import logging
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from wyoming.satellite import RunSatellite
from wyoming.wake import Detection

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

# Global variable for Satellite instance
satellite = None

@app.post("/api/trigger-wake")
async def trigger_wake_word(request: WakeWordTriggerRequest):
    """Triggers wake word detection."""
    if not satellite:
        raise HTTPException(status_code=500, detail="Satellite not initialized")
    
    try:
        # Create a Detection event with optional name and pipeline
        wake_word_name = request.wake_word_name if request.wake_word_name else "web_trigger"
        detection = Detection(
            name=wake_word_name,
            timestamp=0,  # Ignored by the Satellite logic
            pipeline=request.pipeline
        )
        
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
        # Stop streaming if active
        if satellite.is_streaming:
            await satellite.stop_streaming()
            return {"status": "success", "message": "Pipeline cancelled"}
        else:
            return {"status": "info", "message": "No active pipeline"}
            
    except Exception as e:
        _LOGGER.exception("Error canceling pipeline")
        raise HTTPException(status_code=500, detail=str(e))


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
    
    # Parse URI (format: http://host:port)
    try:
        from urllib.parse import urlparse
        parsed_uri = urlparse(api_uri)
        host = parsed_uri.hostname or "127.0.0.1"
        port = parsed_uri.port or 8080
    except Exception as e:
        _LOGGER.error(f"Invalid API URI format: {api_uri}")
        _LOGGER.exception(e)
        host = "127.0.0.1"
        port = 8080
    
    config = uvicorn.Config(app, host=host, port=port)
    server = uvicorn.Server(config)
    
    await server.serve()
