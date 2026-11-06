/* Applies a FreePlay patch package to an official firmware file, in the browser. The same steps as patcher/ppf.py
 * (patched, then appended), so the output is byte-identical to the tested build; the page checks that by SHA-256.
 *
 * A .ppf is Intel HEX text. Only the data bytes and the checksum of the lines that change are rewritten, and new lines
 * are inserted after the image's last line. Every other line stays exactly as it was. */
var FreePlay = (function () {
  'use strict';

  function hexByte(v) {
    return (v < 16 ? '0' : '') + v.toString(16).toUpperCase();
  }

  function hexToBytes(s) {
    var out = new Uint8Array(s.length / 2);
    for (var i = 0; i < out.length; i++) out[i] = parseInt(s.substr(i * 2, 2), 16);
    return out;
  }

  /* the file's lines, and the data records with their full addresses (Number, up to 2^32) */
  function parse(text) {
    var eol = text.slice(0, 4096).indexOf('\r\n') >= 0 ? '\r\n' : '\n';
    var lines = text.split(eol), records = [], upper = 0, seg = 0;
    for (var n = 0; n < lines.length; n++) {
      var line = lines[n];
      if (!line) continue;
      if (line.charAt(0) !== ':') throw new Error('Line ' + (n + 1) + ' is not Intel HEX');
      var count = parseInt(line.substr(1, 2), 16);
      var off = parseInt(line.substr(3, 4), 16);
      var type = parseInt(line.substr(7, 2), 16);
      if (line.length !== 11 + count * 2) throw new Error('Line ' + (n + 1) + ' has a bad length');
      if (type === 0) {
        records.push({ line: n, count: count, off: off, addr: upper + seg + off });
      } else if (type === 2) {
        seg = parseInt(line.substr(9, 4), 16) * 16;
        upper = 0;
      } else if (type === 4) {
        upper = parseInt(line.substr(9, 4), 16) * 65536;
        seg = 0;
      }
    }
    return { eol: eol, lines: lines, records: records };
  }

  /* in-place changes: data bytes and checksum only */
  function patch(f, patches) {
    var want = new Map();
    Object.keys(patches).forEach(function (k) {
      var a = parseInt(k, 16), d = hexToBytes(patches[k]);
      for (var i = 0; i < d.length; i++) want.set(a + i, d[i]);
    });
    var done = 0;
    f.records.forEach(function (r) {
      var hit = false, i;
      for (i = 0; i < r.count; i++) {
        if (want.has(r.addr + i)) { hit = true; break; }
      }
      if (!hit) return;
      var old = f.lines[r.line], b = hexToBytes(old.slice(1)), sum = 0;
      for (i = 0; i < r.count; i++) {
        if (want.has(r.addr + i)) {
          b[4 + i] = want.get(r.addr + i);
          done++;
        }
      }
      for (i = 0; i < 4 + r.count; i++) sum += b[i];
      b[4 + r.count] = (-sum) & 0xff;
      var s = ':';
      for (i = 0; i < b.length; i++) s += hexByte(b[i]);
      f.lines[r.line] = s;
    });
    if (done !== want.size) throw new Error('The firmware file does not have the expected layout');
  }

  function record(bytes) {
    var sum = 0, s = ':', i;
    for (i = 0; i < bytes.length; i++) sum += bytes[i];
    for (i = 0; i < bytes.length; i++) s += hexByte(bytes[i]);
    return s + hexByte((-sum) & 0xff);
  }

  function addressRecord(upper16) {
    return record([2, 0, 0, 4, upper16 >> 8, upper16 & 0xff]);
  }

  /* new lines after the record that ends at `after`; a block crossing 64 KB gets an address record there */
  function append(f, after, blobs) {
    var anchors = f.records.filter(function (r) { return r.addr + r.count === after; });
    if (anchors.length !== 1) throw new Error('The firmware file does not have the expected layout');
    var anchor = anchors[0], base = anchor.addr - anchor.off, cur = base, fresh = [];
    Object.keys(blobs).map(function (k) { return parseInt(k, 16); }).sort(function (x, y) { return x - y; }).forEach(function (a) {
      var data = hexToBytes(blobs['0x' + a.toString(16)]), k = 0;
      while (k < data.length) {
        var ra = a + k, n = Math.min(16, 0x10000 - (ra % 0x10000), data.length - k);
        if (!(cur <= ra && ra < cur + 0x10000)) {
          cur = ra - (ra % 0x10000);
          fresh.push(addressRecord(cur / 0x10000));
        }
        var off = ra - cur, line = [n, off >> 8, off & 0xff, 0];
        for (var i = 0; i < n; i++) line.push(data[k + i]);
        fresh.push(record(line));
        k += n;
      }
    });
    if (cur !== base) fresh.push(addressRecord(base / 0x10000));
    Array.prototype.splice.apply(f.lines, [anchor.line + 1, 0].concat(fresh));
  }

  /* bytes in, bytes out */
  function apply(bytes, pkg) {
    var text = new TextDecoder('ascii').decode(bytes);
    var f = parse(text);
    patch(f, pkg.patches);
    var out = f;
    pkg.appends.forEach(function (ap) {
      /* parse again after each insertion, as ppf.py does: line numbers have moved */
      out = parse(out.lines.join(out.eol));
      append(out, parseInt(ap.after, 16), ap.blobs);
    });
    return new TextEncoder().encode(out.lines.join(out.eol));
  }

  return { apply: apply };
})();
if (typeof module !== 'undefined') module.exports = FreePlay;
