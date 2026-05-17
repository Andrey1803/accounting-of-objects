/**
 * Разбор адреса (BY/RU) для полей паспорта: область, район, нас. пункт, улица, дом.
 */
(function (global) {
  function classifyChunk(ch) {
    var s = String(ch || '').trim();
    if (!s) return null;
    var low = s.toLowerCase();
    if (/область|обл\.\s*$/i.test(low) || /^минск(ая|ий)\s+обл/i.test(s)) return 'region';
    if (/район|р-н|р\.\s*н\.?/i.test(low)) return 'district';
    if (/^ул\.|улица|^пр\.|проспект|^пер\.|переулок|^б-р|бульвар/i.test(s)) return 'street';
    if (/^д\.\s*[A-Za-zА-Яа-яЁё]/i.test(s) && !/^д\.\s*\d/.test(s)) return 'settlement';
    if (/^аг\.|^п\.|^пос\.|^г\.|^с\.|^дер\.|деревня|посёлок|поселок/i.test(s)) return 'settlement';
    if (/^\d+[a-zA-Zа-яА-ЯЁё/-]*$/.test(s) && s.length <= 10) return 'house';
    return null;
  }

  function splitStreetAndHouse(ch) {
    var m = String(ch).match(/^(.+?)\s+(\d+[a-zA-Zа-яА-ЯЁё/-]*)$/);
    if (m && /ул\.|улица|пр\.|пер\.|бульвар|б-р/i.test(m[1])) {
      return { street: m[1].trim(), house: m[2] };
    }
    return { street: ch, house: '' };
  }

  function parseAddressToPassportParts(addr) {
    var chunks = String(addr || '')
      .split(/[,;]/)
      .map(function (s) { return s.trim(); })
      .filter(Boolean);
    var out = { region: '', district: '', settlement: '', street: '', house: '' };
    if (chunks.length === 0) return {};
    if (chunks.length === 1) return { settlement: chunks[0], region: '' };

    var leftovers = [];
  for (var i = 0; i < chunks.length; i++) {
      var raw = chunks[i];
      var ch = raw;
      var extraHouse = '';
      var sh = splitStreetAndHouse(ch);
      if (sh.house) {
        ch = sh.street;
        extraHouse = sh.house;
      }
      var kind = classifyChunk(ch);
      if (kind && !out[kind]) {
        out[kind] = ch;
        if (extraHouse && !out.house) out.house = extraHouse;
      } else if (kind === 'house' && !out.house) {
        out.house = ch;
      } else {
        leftovers.push(raw);
      }
    }

    var order = ['region', 'district', 'settlement', 'street', 'house'];
    for (var j = 0; j < leftovers.length; j++) {
      var ch2 = leftovers[j];
      var kind2 = classifyChunk(ch2);
      if (kind2 && !out[kind2]) {
        out[kind2] = ch2;
        continue;
      }
      if (/^\d+[a-zA-Zа-яА-ЯЁё/-]*$/.test(ch2)) {
        if (!out.house) out.house = ch2;
        else out.house = out.house + '/' + ch2;
        continue;
      }
      for (var k = 0; k < order.length; k++) {
        if (!out[order[k]]) {
          out[order[k]] = ch2;
          break;
        }
      }
    }

    var result = {};
    for (var ki = 0; ki < order.length; ki++) {
      if (out[order[ki]]) result[order[ki]] = out[order[ki]];
    }
    return result;
  }

  global.parseAddressToPassportParts = parseAddressToPassportParts;
})(typeof window !== 'undefined' ? window : globalThis);
