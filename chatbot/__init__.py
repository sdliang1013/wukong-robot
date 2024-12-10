
import os

APP_PATH = os.path.normpath(os.path.dirname(os.path.abspath(__file__)))

def module_resource(so:str):
    return ".".join([__name__,"resources", so])

def resource_file(name:str):
    return os.path.join(APP_PATH, "resources", name)