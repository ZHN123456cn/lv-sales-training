import struct
import os

os.chdir(r'"F:\lv销售培训"')

with open('.git/index', 'rb') as f:
    data = f.read()

print('Index size:', len(data))

pos = 12
num_entries = struct.unpack('>I', data[8:12])[0]
print('Entries in header:', num_entries)

new_entries = []
i = 0
while pos < len(data) - 40:
    mode = struct.unpack('>I', data[pos+24:pos+28])[0]
    flags = struct.unpack('>H', data[pos+60:pos+62])[0]
    path_len = flags & 0xFFF
    
    if path_len > 500:
        break
    
    padded = ((62 + path_len + 7) // 8) * 8
    
    if mode == 0x28000:
        path = data[pos+62:pos+62+path_len].decode('utf-8', errors='replace')
        print('Removing:', path)
    else:
        new_entries.append(data[pos:pos+padded])
    
    pos += padded
    i += 1

print('Processed', i, 'entries, keeping', len(new_entries))

with open('.git/index', 'wb') as f:
    f.write(b'DIRC')
    f.write(struct.pack('>I', 2))
    f.write(struct.pack('>I', len(new_entries)))
    for e in new_entries:
        f.write(e)
    f.write(b'\x00' * 20)

print('Done!')