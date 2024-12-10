import os
from setuptools import setup, find_packages

PKG_PATH = os.path.normpath(os.path.dirname(os.path.abspath(__file__)))

# 版本
app_version = "1.0.0"
with open(file=os.path.join(PKG_PATH, "octopus", "VERSION"), mode="r") as fp:
    app_version = fp.read().strip()
# 包
pkgs = find_packages()
# 数据
pkg_data = {
    "octopus": [
        "*",
        "www/static/*",
        "www/templates/*",
        "resources/*",
        "tools/*",
    ],
}


# 依赖
def _parse_requirements_file(requirements_file):
    parsed_requirements = []
    with open(requirements_file) as rfh:
        for line in rfh.readlines():
            line = line.strip()
            if not line or line.startswith(("#", "-r", "--")):
                continue
            parsed_requirements.append(line)
    return parsed_requirements


setup(
    name="chat-robot",
    version=app_version,
    author="sdliang",
    description="A Chat Robot",
    packages=pkgs,
    package_data=pkg_data,
    install_requires=_parse_requirements_file("requirements.txt"),
    include_package_data=True,
    entry_points={"console_scripts": ["main = octopus.app:main"]},
)
