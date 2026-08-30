from fastapi import FastAPI

app = FastAPI(title="SalesOP")

@app.router('/health')
async def health_check():
    return{
        "status": "OK",
        "message": "Server running"
    }
    