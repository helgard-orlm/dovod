#!/usr/bin/env python3
"""Put page.html into ui.h as a C string (between the PAGE markers). Run after editing page.html."""
import os
d = os.path.dirname(os.path.abspath(__file__))
page = open(os.path.join(d, 'page.html'), encoding='utf-8').read()
lines = []
for ln in page.splitlines():
    esc = ln.replace('\\', '\\\\').replace('"', '\\"')
    lines.append('    "' + esc + '\\n"')
p = os.path.join(d, 'ui.h')
s = open(p, encoding='utf-8').read()
a, b = s.index('/*PAGE-BEGIN*/'), s.index('/*PAGE-END*/')
s = s[:a] + '/*PAGE-BEGIN*/\nstatic const char dv_ui_page[] =\n' + '\n'.join(lines) + ';\n' + s[b:]
open(p, 'w', encoding='utf-8').write(s)
print(f"page.html -> ui.h ({len(page)} bytes)")
