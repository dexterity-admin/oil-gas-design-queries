import uvicorn

from . import config

if __name__ == "__main__":
    print("%s server: http://%s:%d   library: %s" % (config.APP_NAME, config.HOST, config.PORT, config.LIBRARY_DIR))
    uvicorn.run("server.app:app", host=config.HOST, port=config.PORT)
