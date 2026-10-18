import struct
import os

os.chdir(r'F:\lv销售培训')

with open('.git/index', 'rb') as f:
    data = f.read()

print(f'Index size: {len(data)} bytes')

# Find and remove submodule entries (mode 0x28000 = 0o160000)
pos = 12  # Skip header
num_entries = struct.unpack('>I', data[8:12])[0]
print(f'Number of entries: {num_entries}')

new_entries = []
i = 0
while pos < len(data) - 20:
    try:
        mode = struct.unpack('>I', data[pos+24:pos+28])[0]
        flags = struct.unpack('>H', data[pos+60:pos+62])[0]
        path_len = flags & 0xFFF
        
        if path_len > 500:
            print(f'Entry {i}: unusual path_len={path_len}, mode={oct(mode)}')
            break
        
        padded_len = ((62 + path_len + 7) // 8) * 8
        entry_end = pos + padded_len
        
        if mode == 0x28000:  # Submodule
            path = data[pos+62:pos+62+path_len].decode('utf-8', errors='replace')
            print(f'Removing submodule: {path}')
        else:
            new_entries.append(data[pos:entry_end])
        
        pos = entry_end
        i += 1
        
        if i > 60:
            print('Too many entries, stopping')
            break
    except Exception as e:
        print(f'Error at entry {i}, pos={pos}: {e}')
        break

print(f'Processed {i} entries, kept {len(new_entries)}')

# Write new index
with open('.git/index.new', 'wb') as f:
    f.write(data[:12])  # header
    f.write(struct.pack('>I', len(new_entries)))
    for e in new_entries:
        f.write(e)

os.replace('.git/index.new', '.git/index')
print('Index rewritten!')