from fastapi import FastAPI

app = FastAPI(title="SalesOP")

@app.get('/health')
async def health_check():
    return{
        "status": "OK",
        "message": "Server running"
    }
    