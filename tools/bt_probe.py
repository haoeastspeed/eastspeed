# -*- coding: utf-8 -*-
"""BT 探针：验证 libtorrent 本机 seeder -> downloader（.torrent 与 magnet）全链路。
仅用于开发期确认 API，不进入发行包。"""
import hashlib
import os
import shutil
import tempfile
import time

import libtorrent as lt

print("libtorrent", lt.version)

root = tempfile.mkdtemp(prefix="btprobe-")
seeddir = os.path.join(root, "seed")
dldir = os.path.join(root, "dl")
os.makedirs(seeddir)
os.makedirs(dldir)

NAME = "payload.bin"
payload = os.urandom(512 * 1024 + 123)
with open(os.path.join(seeddir, NAME), "wb") as f:
    f.write(payload)
expect = hashlib.sha256(payload).hexdigest()

# ---- 造种子（v1，单文件）----
fs = lt.file_storage()
fs.add_file(NAME, len(payload))
flags = 0
for cand in ("v1_only",):
    v = getattr(lt.create_torrent, cand, None)
    if v is not None:
        flags = int(v)
ct = lt.create_torrent(fs, 0, flags)
lt.set_piece_hashes(ct, seeddir)
tor_bytes = lt.bencode(ct.generate())
tor_path = os.path.join(root, "a.torrent")
with open(tor_path, "wb") as f:
    f.write(tor_bytes)
info = lt.torrent_info(tor_path)
mag = lt.make_magnet_uri(info)
print("magnet:", mag[:90], "...")
print("files:", info.files().num_files(), "pieces:", info.num_pieces())

SEED_PORT = 6897


def make_session(iface):
    s = lt.session({
        "listen_interfaces": iface,
        "enable_dht": False, "enable_upnp": False, "enable_natpmp": False,
        "enable_lsd": False, "alert_mask": 0,
    })
    return s


# ---- seeder ----
ss = make_session(f"127.0.0.1:{SEED_PORT}")
sh = ss.add_torrent({"ti": info, "save_path": seeddir})
deadline = time.time() + 20
while time.time() < deadline:
    st = sh.status()
    if st.is_seeding or st.state == lt.torrent_status.seeding:
        break
    time.sleep(0.2)
print("seeder state:", sh.status().state, "is_seeding:", sh.status().is_seeding)


def run_download(label, add):
    ds = make_session("127.0.0.1:0")
    h = add(ds)
    h.connect_peer(("127.0.0.1", SEED_PORT))
    got_meta = False
    t0 = time.time()
    while time.time() - t0 < 40:
        st = h.status()
        if st.torrent_file is not None:
            got_meta = True
        if st.progress >= 1.0 and st.is_seeding:
            break
        time.sleep(0.2)
    st = h.status()
    out = os.path.join(dldir, label.replace(":", "_"))
    os.makedirs(out, exist_ok=True)
    # downloader save_path 各自独立，避免互相覆盖
    ok = st.progress >= 1.0
    print(f"[{label}] meta={got_meta} progress={st.progress:.3f} "
          f"state={st.state} peers={st.num_peers} complete={ok}")
    return ds, ok


# .torrent 下载（独立目录）
dl1 = os.path.join(root, "dl_torrent")
os.makedirs(dl1, exist_ok=True)


def add_ti(s):
    return s.add_torrent({"ti": lt.torrent_info(tor_path),
                          "save_path": dl1})


ds1, ok1 = run_download("torrent", add_ti)
p1 = os.path.join(dl1, NAME)
h1 = hashlib.sha256(open(p1, "rb").read()).hexdigest() if os.path.exists(p1) else ""
print("  .torrent sha256 match:", h1 == expect)

# magnet 下载
dl2 = os.path.join(root, "dl_magnet")
os.makedirs(dl2, exist_ok=True)


def add_mag(s):
    return lt.add_magnet_uri(s, mag, {"save_path": dl2})


ds2, ok2 = run_download("magnet", add_mag)
p2 = os.path.join(dl2, NAME)
h2 = hashlib.sha256(open(p2, "rb").read()).hexdigest() if os.path.exists(p2) else ""
print("  magnet sha256 match:", h2 == expect)

print("RESULT", "PASS" if (ok1 and ok2 and h1 == expect and h2 == expect)
      else "FAIL")
shutil.rmtree(root, ignore_errors=True)
