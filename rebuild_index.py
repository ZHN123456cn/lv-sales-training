import struct
import os

os.chdir(r'F:\lv销售培训')

# Read all tracked files (excluding submodules)
result = os.popen('git ls-tree -r --name-only HEAD').read()
files = [f.strip() for f in result.split('\n') if f.strip()]

print(f'Found {len(files)} files in HEAD')

entries = []
for f in files:
    if 'project_class' in f:
        print(f'Skipping: {f}')
        continue
    try:
        blob = os.popen(f'git ls-tree -r HEAD -- "{f}"').read().strip()
        if blob:
            parts = blob.split()
            mode = int(parts[0])
            sha = parts[2]
            if mode != 0o160000:
                entries.append((mode, sha, f))
    except Exception as e:
        print(f'Error processing {f}: {e}')

print(f'Keeping {len(entries)} entries')

# Write new index
with open('.git/index', 'wb') as f:
    f.write(b'DIRC')
    f.write(struct.pack('>I', 2))
    f.write(struct.pack('>I', len(entries)))
    
    for mode, sha, path in sorted(entries, key=lambda x: x[2]):
        entry = bytearray(62 + len(path.encode('utf-8')))
        struct.pack_into('>I', entry, 24, mode)
        sha_bytes = bytes.fromhex(sha)
        entry[40:60] = sha_bytes
        struct.pack_into('>H', entry, 60, len(path.encode('utf-8')))
        entry[62:] = path.encode('utf-8')
        f.write(entry)
    
    f.write(b'\x00' * 20)

print('Index rebuilt!')