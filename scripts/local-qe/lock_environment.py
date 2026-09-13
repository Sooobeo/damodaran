"""Freeze resolved Windows wheel versions and hashes from pip installation reports."""
from common import QE_ROOT, ROOT, read_json


def main():
    packages = {}
    for filename in ('install-torch.json', 'install-packages.json'):
        for item in read_json(QE_ROOT / filename)['install']:
            name = item['metadata']['name'].lower().replace('_', '-')
            packages[name] = (item['metadata']['version'], item['download_info']['archive_info']['hashes']['sha256'])
    output = ROOT / 'scripts/local-qe/requirements.windows.lock.txt'
    with output.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write('# Exact wheels installed on Windows CPython 3.11; CPU runtime only.\n')
        for name, (version, digest) in sorted(packages.items()):
            stream.write(f'{name}=={version} --hash=sha256:{digest}\n')
    print(f'Locked {len(packages)} resolved package wheels.')


if __name__ == '__main__':
    main()
