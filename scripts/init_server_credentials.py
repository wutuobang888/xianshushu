"""新服务器交互创建网站凭据和孔夫子加密密钥，不覆盖已有文件。"""
import argparse
import getpass
import json
import os
from pathlib import Path

from cryptography.fernet import Fernet
from werkzeug.security import generate_password_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--access-file', type=Path, default=Path('/etc/xianshushu/web-access.json'))
    parser.add_argument('--key-file', type=Path, default=Path('/etc/xianshushu/kongfz.key'))
    args = parser.parse_args()
    paths = (args.access_file, args.key_file)
    if paths[0].resolve() == paths[1].resolve() or any(p.exists() for p in paths):
        raise SystemExit('凭据文件已存在或路径重复，请保留原配置，不要覆盖。')
    if not all(p.parent.is_dir() for p in paths):
        raise SystemExit('请先创建凭据目录并设置访问权限。')
    username = input('网站账号: ').strip()
    password = getpass.getpass('网站密码: ')
    confirmation = getpass.getpass('再次输入密码: ')
    if not username or not password or password != confirmation:
        raise SystemExit('账号为空或两次密码不一致，未保存。')
    credential = json.dumps({'username': username, 'password_hash': generate_password_hash(password)}).encode()
    # 独占创建且初始权限仅当前用户可读写；不输出密码或密钥。
    for path, value in zip(paths, (credential, Fernet.generate_key())):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(value)
    print('新实例凭据已创建。请按部署说明设置 root:www-data 和 0640 权限。')


if __name__ == '__main__':
    main()
