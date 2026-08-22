import requests, time, re, sys

m = 'https://docker.1ms.run'
hdr = {'Accept': 'application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.index.v1+json'}
requests.adapters.DEFAULT_RETRIES = 0


def out(s):
    print(s, flush=True)


def fetch(path, hdrs, timeout=15):
    r = requests.get(f'{m}/v2/library/elasticsearch/{path}', headers=hdrs, timeout=timeout)
    if r.status_code == 401:
        ww = r.headers.get('WWW-Authenticate', '')
        mt = re.search(r'realm="([^"]+)"', ww)
        st = re.search(r'service="([^"]+)"', ww)
        sc = re.search(r'scope="([^"]+)"', ww)
        if mt and st and sc:
            tok = requests.get(f'{mt.group(1)}?service={st.group(1)}&scope={sc.group(1)}', timeout=10).json().get('token', '')
            if tok:
                hdrs = dict(hdrs, Authorization=f'Bearer {tok}')
                r = requests.get(f'{m}/v2/library/elasticsearch/{path}', headers=hdrs, timeout=timeout)
    return r, hdrs

out(f'test mirror: {m}')
r, hdrs = fetch('manifests/8.11.0', hdr)
out(f'manifests -> {r.status_code}')
if r.status_code != 200:
    sys.exit(1)
data = r.json()
if 'manifests' in data:
    amd64 = [x for x in data['manifests'] if x.get('platform', {}).get('architecture') == 'amd64'] or data['manifests']
    digest = amd64[0]['digest']
    out(f'amd64 digest={digest[:30]}')
    r2, hdrs = fetch(f'manifests/{digest}', hdr)
    data = r2.json()
layers = data.get('layers', [])
big = max(layers, key=lambda l: l['size'])
out(f'layers={len(layers)} biggest={big["size"]/1e6:.0f}MB')
url = f'{m}/v2/library/elasticsearch/blobs/{big["digest"]}'
t = time.time()
try:
    resp = requests.get(url, headers=dict(hdrs, Range='bytes=0-8388607'), timeout=20, stream=True)
    n = 0
    for chunk in resp.iter_content(262144):
        n += len(chunk)
        if n >= 8388608:
            break
    dt = time.time() - t
    out(f'blob: HTTP {resp.status_code} {n/1e6:.1f}MB in {dt:.1f}s = {n/dt/1e6*8:.1f} Mbps')
except Exception as e:
    out(f'blob FAIL: {type(e).__name__} {str(e)[:80]}')
