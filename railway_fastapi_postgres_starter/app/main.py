from fastapi import FastAPI
from .database import engine
from .models import Base

app = FastAPI(title="Railway FastAPI Starter")

Base.metadata.create_all(bind=engine)

@app.get("/")
def root():
    return {"status": "running"}

@app.get("/health")
def health():
    return {"ok": True}
