# ==========================================================
# FILE: app/main_minimal.py (MINIMAL TEST VERSION)
# PURPOSE: Test if FastAPI can respond to ANY request
# ==========================================================

from fastapi import FastAPI
from fastapi.responses import JSONResponse
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create app with NO middleware, NO dependencies
app = FastAPI(title="Minimal Test", version="1.0.0")

# Simplest possible endpoint
@app.get("/test")
async def test():
    logger.info("✅ /test endpoint hit")
    return {"status": "working", "message": "Raw endpoint works!"}

@app.get("/alive")
async def alive():
    logger.info("✅ /alive endpoint hit")
    return {"alive": True}

@app.get("/")
async def root():
    logger.info("✅ / endpoint hit")
    return {"status": "ok", "message": "Server is running"}

# Global exception handler to catch ALL errors
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    import traceback
    error_details = traceback.format_exc()
    logger.error(f"💥 GLOBAL CATCH: {request.url.path}\n{error_details}")
    return JSONResponse(
        status_code=500,
        content={
            "error": str(exc),
            "type": type(exc).__name__,
            "path": request.url.path
        }
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main_minimal:app", host="0.0.0.0", port=8000, reload=True)
