"""Retter sammenlimede ord og Niño/Niña i OCR-tekstlaget i en ocrmypdf-PDF.
Brug: python3 ocr_ret.py ind.pdf ud.pdf log.txt [--dry]
Kræver: pip install pikepdf spylls  (dansk ordbog ligger i ordbog/)"""
import sys, re, collections, pikepdf
from spylls.hunspell import Dictionary

D = Dictionary.from_files(str(__import__('pathlib').Path(__file__).parent / 'ordbog' / 'da'))
_c = {}
def known(w):
    if w not in _c:
        try: _c[w] = bool(D.lookup(w) or D.lookup(w.lower()) or D.lookup(w.capitalize()))
        except Exception: _c[w] = False
    return _c[w]

F = set('i at og af en et de er på til det der som for med om ikke sig kan man den ved har var fra så ud op vi jeg dog nok jeres deres hans sin sit vores dette denne disse hvor men også eller'.split())
def isF(p): return p.lower() in F

_s = {}
def strict(w):
    if w not in _s:
        try: _s[w] = any(True for _ in D.lookuper.good_forms(w, compound_forms=False)) or any(True for _ in D.lookuper.good_forms(w.lower(), compound_forms=False))
        except Exception: _s[w] = False
    return _s[w]

SUFX = set('en er de et ud om vi den der man sin sit hans var så op ved dog nok'.split())
def ok_other(p): return isF(p) or (len(p) >= 4 and known(p))

def valid(ps):
    if not any(isF(p) for p in ps): return False
    if not all(ok_other(p) for p in ps): return False
    last = ps[-1]
    if last.lower() in SUFX and not isF(ps[-2]): return False
    return True

def best_split(w):
    n = len(w); lw = w.lower(); cands = []
    cap = w[0].isupper() and not w.isupper()
    # 2 dele
    for f in F:
        if lw.startswith(f) and len(w) > len(f): cands.append([w[:len(f)], w[len(f):]])
        if lw.endswith(f) and len(w) > len(f): cands.append([w[:n-len(f)], w[n-len(f):]])
    # 3 dele
    for f1 in F:
        for f2 in F:
            if lw.startswith(f1 + f2) and n > len(f1+f2):
                cands.append([w[:len(f1)], w[len(f1):len(f1+f2)], w[len(f1+f2):]])
            if lw.endswith(f1 + f2) and n > len(f1+f2):
                k = n - len(f1+f2); cands.append([w[:k], w[k:k+len(f1)], w[k+len(f1):]])
            if lw.startswith(f1) and lw.endswith(f2) and n > len(f1) + len(f2):
                cands.append([w[:len(f1)], w[len(f1):n-len(f2)], w[n-len(f2):]])
    good = []
    for ps in cands:
        if not valid(ps): continue
        if cap and not isF(ps[0]):
            if not (len(ps) == 2 and ps[1] == 'i' and len(ps[0]) >= 6 and strict(ps[0])): continue
        allstrict = all(isF(p) or strict(p) for p in ps)
        good.append((0 if allstrict else 1, len(ps), -max([len(p) for p in ps if not isF(p)] or [0]), ps))
    if not good: return None
    # ser ordet ud som et gyldigt sammensat ord? så lad det være
    for i in range(3, n - 2):
        a, b = w[:i], w[i:]
        if not isF(a) and not isF(b) and strict(a) and strict(b): return None
    good.sort(key=lambda c: c[:3])
    return ' '.join(good[0][3])

# manuelle undtagelser efter gennemgang af prøvekørsel (None = rør ikke)
OVERRIDES = {'senaderen': None, 'revlerud': None, 'Kanariestrømmen': None, 'vulkaneren': None,
  'Envisats': None, 'Formason': None, 'endiresten': None, 'Departmen': None, 'detresultere': None,
  'derforer': 'derfor er', 'Afbrændingaf': 'Afbrænding af', 'etstortantal': 'et stort antal',
  'detertilfældeti': 'det er tilfældet i', 'istedet': 'i stedet', 'lokaleindbyggerehar': 'lokale indbyggere har',
  'lokkerturistertil': 'lokker turister til', 'fravestmod': 'fra vest mod', 'ogjerner': 'og fjerner'}
NINO = re.compile(r'^(Ni)(?:fi|ri|f|r)([oa])(s?)$')
NINO2 = re.compile(r'^(Ni)(?:h|n|fi|ri|f|r)([oa])(s?)$')
def correct(core, prev=''):
    if core in OVERRIDES: return OVERRIDES[core]
    m = NINO.match(core) or (prev.strip() in ('El', 'La') and NINO2.match(core))
    if m: return m.group(1) + ('ñ') + m.group(2) + m.group(3)
    if len(core) < 4 or not core.isalpha() or known(core): return None
    return best_split(core)

inp, outp, logp = sys.argv[1:4]; dry = '--dry' in sys.argv
pdf = pikepdf.open(inp)

# find den fælles OCR-font og byg kodetabeller
font = None
for pg in pdf.pages:
    fr = pg.Resources.get('/Font')
    if fr:
        for k, f in fr.items(): font = f; break
    if font is not None: break
cmap = font.ToUnicode.read_bytes().decode('latin1')
dec, enc = {}, {}
for a, b, u in re.findall(r'<([0-9a-fA-F]{4})>\s*<([0-9a-fA-F]{4})>\s*<([0-9a-fA-F]{4})>', cmap):
    a, b, u = int(a, 16), int(b, 16), int(u, 16)
    for k in range(b - a + 1):
        dec[a + k] = chr(u + k); enc.setdefault(chr(u + k), a + k)
for c, u in re.findall(r'<([0-9a-fA-F]{4})>\s*<([0-9a-fA-F]{4})>(?!\s*<)', cmap.split('beginbfchar')[1] if 'beginbfchar' in cmap else ''):
    dec[int(c, 16)] = chr(int(u, 16)); enc.setdefault(chr(int(u, 16)), int(c, 16))
desc = font.DescendantFonts[0]
DW = float(desc.get('/DW', 1000))
widths = {}
W = list(desc.get('/W', []))
i = 0
while i < len(W):
    s = int(W[i])
    if isinstance(W[i + 1], pikepdf.Array):
        for k, w in enumerate(W[i + 1]): widths[s + k] = float(w)
        i += 2
    else:
        for k in range(int(W[i]), int(W[i + 1]) + 1): widths[k] = float(W[i + 2])
        i += 3
def width(codes): return sum(widths.get(c, DW) for c in codes)

added = {}
def code_for(ch):
    if ch in enc: return enc[ch]
    # tilføj nyt tegn (ñ) som ny CID, der genbruger glyffen fra 'n'
    base = enc['n']; new = max(dec) + 1
    dec[new] = ch; enc[ch] = new; widths[new] = widths.get(base, DW); added[new] = (ch, base)
    return new

def decode(bs): return ''.join(dec.get(int.from_bytes(bs[k:k+2], 'big'), '�') for k in range(0, len(bs), 2))
def encode(s): return [code_for(ch) for ch in s]

log = []; stats = collections.Counter()
for pno, pg in enumerate(pdf.pages, 1):
    if '/Font' not in pg.Resources: continue
    ops = pikepdf.parse_content_stream(pg)
    out = []; prev_hyph = False; prev_txt = ''; tm_idx = None; changed = False
    for operands, op in ops:
        opn = str(op)
        if opn == 'Tm': tm_idx = len(out)
        if opn == 'Tj' and isinstance(operands[0], pikepdf.String):
            raw = bytes(operands[0]); txt = decode(raw)
            m = re.match(r'^([\W\d_]*)([^\W\d_]+)([\W\d_]*)$', txt)
            new = None; orig = fix = None
            hy = txt.rstrip().endswith(('-', '¬', '—'))
            ctx = prev_txt.strip() in ('El', 'La')
            t2 = re.sub(r'^(El|La)(Ni)', r'\1 \2', txt)
            t2 = re.sub(r'Ni(?:fi|ri|f|r' + ('|h' if (ctx or t2 != txt) else '') + r')(?=[oa](?:s\b|\b|[-—]|$))', 'Niñ', t2)
            if t2 != txt:
                new = t2; orig = txt.strip(); fix = t2.strip()
            elif m and not prev_hyph and not hy:
                f2 = correct(m.group(2), prev_txt)
                if f2 and f2 != m.group(2):
                    new = m.group(1) + f2 + m.group(3); orig = m.group(2); fix = f2
            prev_hyph = txt.rstrip().endswith(('-', '¬')); prev_txt = txt
            if new:
                codes = encode(new); ow = width([int.from_bytes(raw[k:k+2], 'big') for k in range(0, len(raw), 2)]); nw = width(codes)
                if tm_idx is not None and nw > 0:
                    tops, _ = out[tm_idx]; tops = list(tops)
                    tops[0] = float(tops[0]) * ow / nw
                    out[tm_idx] = (tops, pikepdf.Operator('Tm'))
                operands = [pikepdf.String(b''.join(c.to_bytes(2, 'big') for c in codes))]
                log.append(f'side {pno}: {orig} → {fix}'); stats[(orig, fix)] += 1; changed = True
        out.append((operands, op))
    if changed and not dry:
        pg.Contents = pdf.make_stream(pikepdf.unparse_content_stream(out))

if added and not dry:
    # udvid ToUnicode, W og CIDToGIDMap
    extra = ''.join(f'<{c:04x}> <{ord(ch):04x}>\n' for c, (ch, _) in added.items())
    cm = cmap.replace('endcmap', f'{len(added)} beginbfchar\n{extra}endbfchar\nendcmap', 1)
    font.ToUnicode.write(cm.encode('latin1'))
    for c, (ch, base) in added.items():
        desc.W.append(c); desc.W.append(pikepdf.Array([widths[c]]))
    c2g = desc.get('/CIDToGIDMap')
    if isinstance(c2g, pikepdf.Stream):
        m = bytearray(c2g.read_bytes())
        for c, (ch, base) in added.items():
            g = m[base*2:base*2+2]
            while len(m) < (c+1)*2: m += b'\0\0'
            m[c*2:c*2+2] = g
        c2g.write(bytes(m))
if not dry:
    pdf.save(outp)
with open(logp, 'w') as f:
    f.write(f'Rettelser i alt: {len(log)}\n\nOptælling:\n')
    for (a, b), n in stats.most_common(): f.write(f'{n:4d}  {a} → {b}\n')
    f.write('\nPr. side:\n' + '\n'.join(log) + '\n')
print('rettelser:', len(log), 'unikke:', len(stats), 'nye tegn:', added)
