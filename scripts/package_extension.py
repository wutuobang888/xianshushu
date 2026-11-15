"""仅打包固定扩展源码；不包含任何数据库、登录态或凭据。"""
import argparse
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

FILES = ('manifest.json','policy.js','background.js','content.js','address-engine.js','collector.js',
         'popup.html','popup.js','PRIVACY.md','INSTALL.md','LICENSE')


def package(destination):
    source = Path(__file__).resolve().parents[1] / 'browser-extension'
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(destination, 'w', ZIP_DEFLATED) as archive:
        for name in FILES:
            archive.write(source/name, name)
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('output', help='扩展 ZIP 保存位置')
    args = parser.parse_args()
    print(package(args.output))
