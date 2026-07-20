import uvicorn

from .config import settings


if __name__ == "__main__":
    uvicorn.run("app.main:app", host=settings.listen_host, port=settings.listen_port, reload=False)
