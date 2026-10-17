# Bayat Bağlam (Stale Context) — Tam Araştırma ve Düzeltme Planı

> **Durum:** **UYGULANDI (P0–P7 + F-13 takibi).** Bu dosya araştırma çıktısıdır; uygulaması §9'daki kayıt zinciriyle yapıldı — güncel durum için §12 (board düzlemi) ve §13 (workflow kanıt düzlemi, F-13) bölümlerine bak.
> **Tetikleyici:** bir LinkedIn yorumu — *"The failure mode we hit most isn't lost context, it's stale context. Yesterday's decision is still in the handoff file, and the agent obeys it after the code has already moved on. Our fix was dating every recorded decision to a commit, so a handoff can say 'this no longer applies' instead of just repeating the past."*
> **Ölçüm tarihi:** 2026-10-16, metodoloji dogfood reposu (48 E kaydı, `.metodoloji/blackboard.json` canlı board).
> **Kapanış:** 2026-10-16 — E-052 (baseline, REJECTED, measured=1.0) → E-053 (fix, APPROVED, measured=0.0) → IR-002 → SP-005 → S-002 → QR-017; TD-016 ödendi (`docs/development/tech-debt.md`). Değişiklik land edildi (`d715318`, v0.1.55); E-053 landed commit **öncesi** kirli ağaca tarihlendiği için tazelik düzlemi onu haklı olarak `STALE` gösterir — landed durumda yeniden ölçüm **E-054** (APPROVED + CURRENT) ve E-053 `Re-Measured-By: E-054` ile süpersede (CASCADE_INVALIDATION).

---

## 0. Verdict

Yorum haklı ve metodolojide tek bir instance'a değil, **beş ayrı kök nedene** denk geliyor. Metodoloji "kayıp bağlam"ı mekanik olarak çözüyor; "bayat bağlam"ı **iki vekille** çözmeye çalışıyor:

1. **Yaş (TTL).** Bir sinyalin geçerliliği, yazıldığından bu yana geçen süreye indirgenmiş.
2. **İmza.** Bir kaydın gerçekliği HMAC token'la kanıtlanıyor — ama token yalnızca **kayıt-içi** olguları (komut, kapsam, iddia, plan hash'i) bağlıyor; **kaydın doğru olduğu kod durumunu** hiçbir alan taşımıyor.

Bunun sonucu: sistemde *"bu hâlâ geçerli mi?"* sorusunu cevaplayan **tek bir yüzey yok**. Okuma yolları yaş kontrolü bile yapmıyor (§2 F-01), enjeksiyon katmanı bayat işi **canlı iş gibi** duyuruyor (F-02), onaylı kapsam evreni **monoton** büyüyor (F-05), ve tazelik için tasarlanmış invalidation düzlemi **deklare edilip bağlanmamış** durumda (F-06).

Düzeltmenin adı: **Tazelik Düzlemi (Freshness Plane)** — her iddiaya bir *tanık (witness)* eklemek ve tazeliği **saklamak yerine türetmek**, üç değerli bir verdict'le (`CURRENT | STALE | UNKNOWN`) ve birinci sınıf bir **emeklilik (retirement)** protokolüyle. Tasarım §4, plan §6.

---

## 1. Yorumun genellemesi

Yorumun tarif ettiği zincir şudur:

```
karar yazıldı → kod o kararı geçersiz kılacak şekilde değişti → karar hâlâ okunuyor → ajan ona itaat ediyor
```

Metodolojide bu zincirin **her halkası** ayrı ayrı mevcut, ve halkalar arasında hiçbir bağ yok:

| Halka | Metodolojideki karşılığı | Bağ |
|---|---|---|
| karar yazıldı | handoff sinyali (`post_handoff`), board anahtarı (`write_key`), E kaydı | yalnız `ts` (duvar saati) |
| kod değişti | guard/quality/deploy yazma-anı kontrolleri, `git diff --name-only HEAD` | karara bağlanmıyor |
| karar hâlâ okunuyor | `pending_handoffs`, `session_start` inject, `chain_health` | yaş kontrolü yok (F-01) |
| ajan itaat ediyor | inject metni: *"a run finished its work but nobody picked up the baton"* | "hâlâ geçerli" iddiası sorgusuz |

**Yorumun fix'i ("her kararı bir commit'e tarihle") metodolojide eksik olan tek şey:** kaydın *doğru olduğu andaki kod durumu*. Bugün hiçbir kalıcı yapı bunu taşımıyor.

---

## 2. Ölçülen durum — kanıt envanteri

Her bulgu: **iddia → kanıt (file:line) → sonuç → şiddet**.

### F-01 — Okuma yolu yaş kontrolsüz; TTL yazma-tetiklemeli (KRİTİK)

- `pending_handoffs` (`hooks/engine/modules/blackboard.py:1641-1645`), `pending_handoff_channels` (`:1648-1673`), `recent_handoffs` (`:1676-1715`): üçü de **yaşa hiç bakmıyor**; board'daki handoff alert'lerini olduğu gibi döndürüyor.
- TTL yalnızca **yazma yolunda** uygulanıyor: `_apply_event`'in alert dalı (`:685`) ve `_mutate` (`:875-890`).
- **Sonuç:** TTL "yazma tetiklemeli"dir. Board boşta kaldığı sürece bayat sinyal *süresiz* okunur; ilk okuyucu onu temizlenmemiş hâlde görür. Canlı kanıt: F-11'de 43 saatlik sinyaller hâlâ okunur durumda (son mutasyon 20.4 saat önceydi, o an yaş < 24 saat olduğu için temizlenmemişti).
- **Bu, yorumun arızasının çekirdeğidir.**

### F-02 — `session_start` inject'i bayat işi canlı iş gibi duyuruyor (KRİTİK)

- `audit.session_start` (`hooks/engine/modules/audit.py:337-470`): `waiting_s` metni **yaş içermiyor** ve işi canlı olarak çerçeveliyor:
  `"<skill> (n): n unclaimed signal(s) from completed upstream runs — a run finished its work but nobody picked up the baton; peek … then consume …"` (`:400-415`).
- Aynı inject'te **"Chain progress — E: …, IR: …"** satırı `compact_context(...)["methodology"]`'den geliyor (`:420-425` ← `blackboard.py:2733-2747`); `methodology.last_*` değerleri **yaşsız** basılıyor.
- **Sonuç:** her yeni oturum, günler öncesinin kararını "yapılacak iş" olarak devralır. Yorumun tarif ettiği an tam burasıdır. Enjeksiyon katmanı, tazelik açığının **yükselticisidir** (amplifier).

### F-03 — En dayanıklı yüzey, en az denetlenen yüzeydir (YÜKSEK)

- `_BRIDGE_KEYS` (`blackboard.py:779-788`) `scope`, `status` ve `methodology.last_*` anahtarlarını **cap tahliyesinden korur**; `_METHODOLOGY_MIRROR_KEYS` (`:790`) mirror'ını `write_key` yazar (`:967-980`), run-açılış heartbeat'i ve watcher damgası da aynı yerde (`:988-1030`).
- `methodology.last_*`'ı **hiçbir şey geçersiz kılmaz**: ne cap, ne `forget`, ne bir emeklilik event'i.
- **Sonuç:** en kalıcı, en çok enjekte edilen ve en az doğrulanan yüzey aynı yüzeydir. `E-*` çalışma anahtarları cap'le düşebilir ama `last_*` sonsuza dek kalır.

### F-04 — Kayıt imzası *tazelik* değil, *kurcalanma* kanıtıdır (YÜKSEK)

- Token bağlaması `run_experiment.py:1240-1255`: `gate_token(claim, val, did, secret, args.run, fields["Code Scope"], plan_hash)`. `verify()` (`:1277-1360`) kayıt **içi** olguları yeniden türetir ve oynanmışsa `FORGED` der.
- **Hiçbir alan "bu onay hangi kod durumunda doğruydu?" sorusunu taşımıyor.** Bir kez `APPROVED` olan kaydın kapsamı, kod arada tamamen yeniden yazılmış olsa bile **süresiz açık** kalır.
- `_check_scope_coverage` (`guard.py:2027-2060`) git'i yalnızca *şu anki* work-tree/untracked dosyaları saymak için kullanır (`:2057`); `HEAD` bir diff tabanıdır, karar çapası değil.
- **Sonuç:** ajan, "dünün kararına kod ilerlemişken itaat eder" — handoff dosyasının bir kat üstünde, **kaydın kendisinde**.

### F-05 — Onaylı kapsam evreni monoton bir birleşimdir (YÜKSEK)

- `scripts/check-plugin.sh` §3b: `scopes` = **bütün** `APPROVED` kayıtların `Code Scope`'ları; `covered = any(...)` (`:1101-1137`); raporda `approved scopes in force: {len(scopes)}` (`:1134`).
- Hiçbir kaydın kapsamı **süresi dolmaz**; hiçbir kapsam bir kod durumuna bağlı değildir.
- Dogfood'da bu kontrol **atlanıyor**: `"dogfood repo (project root == methodology root) — scope coverage gate not applicable"` (`:1050`).
- **Sonuç:** 48 kaydın her biri kalıcı bir açık kapı bırakıyor (`src/**` gibi bir glob bir kez girdiyse çıkmıyor), ve metodoloji kendi reposunda bu kontrolü koşmuyor.

### F-06 — Invalidation düzlemi deklare edilmiş, bağlanmamış (ORTA)

- `AlertKind.CASCADE_INVALIDATION`, `AlertKind.STALE_SESSION` (`blackboard.py:49-50`): repo genelinde **sıfır üretici, sıfır tüketici** (grep).
- `guard.py:356`'da `cascade_warnings = []` ilan edilir, **hiç eklenmez, hiç döndürülmez**; üstündeki yorum (`:320`) "HIGH #6 / ISSUE #12: Rollback/cascade invalidation" diye **var olmayan** bir mekanik anlatır.
- **Sonuç:** "üstteki karar değişti → alttaki bayat" fikri tasarlanmış, kodda yarım bırakılmış; sevk edilen tek şey TTL. Bu, projenin kendi yinelenen sınıfıdır (E-037: *kimsenin ölçmediği bir olguyu iddia eden artifact*).

### F-07 — Doğrulama cache'i: cross-process etkisiz + invalidation çağrılmıyor (ORTA)

- `_VERIFY_CACHE` / `_VERIFY_CACHE_TTL_SECONDS = 300` / `invalidate_verify_cache()` (`guard.py:1019-1058`).
- `invalidate_verify_cache` **hiçbir yerden çağrılmıyor** (yalnız tanım; testlerde yalnız `_VERIFY_CACHE.clear()`).
- Hook engine **çağrı başına soğuk başlatılır** (aynı dosyada, `mirror.py:220-221`'de açıkça yazılı: *"the hook engine is cold-started per call, so an in-process throttle never fires"*). Aynı mantık bu cache için de geçerli: process-içi bir sözlük, çağrılar arası hiçbir şey taşımaz.
- **Sonuç:** "5 dakikalık doğrulama cache'i" fiilen ölü bir mekanizmadır; ölü olduğu için de kimse ondan tazelik beklemez — ama dokümanlarda var gibi okunur.

### F-08 — Süresi dolmayan alert'ler (ORTA)

- Scope debt ve session alert'leri yalnızca **remediation** (E onayı / temiz commit) ya da elle `consume` ile temizlenir (`audit.py:445-470`); yaş bilgisi taşınmaz ve otomatik eskime yok.
- **Sonuç:** temizlenmeyen bir borç kalıcı bir "canlı uyarı" hâline gelir — yine F-02'nin yükseltici etkisi.

### F-09 — Ölü doküman referansları (DÜŞÜK — ama sınıfın kanıtı)

- `blackboard.py:21` hâlâ `docs/BLACKBOARD.md`'yi "Design invariants" kaynağı olarak gösteriyor; dosya **prune edilmiş** (`GUIDE.md:1119`, `KILAVUZ.md:1118`: *"eski `docs/BLACKBOARD.md` prune edildi — kod kanoniktir"*).
- `audit.py:461`, `stop.py:439`, `test_blackboard.py:2596,2610` hâlâ `BLACKBOARD.md §5`'e atıf yapıyor.
- **Sonuç:** geçmişi tekrarlayan, var olmayan bir dosyaya dayanan canlı yüzeyler — yorumun anlattığı ataletin **kendi reposundaki kanıtı**.

### F-10 — Relay throttle'ı yaşı yok saymak üzerine kurulu (ORTA)

- `mirror.py:227-250`: digest, nottaki `"(Nm ago)"` damgalarını **soyarak** karşılaştırma yapar; gerekçe yazılı: *"note embeds '(Nm ago)' stamps that roll forward every minute, so a note-text comparison re-announced an unchanged relay at every minute boundary."*
- Tekrarlamayı önlemek için **doğru** davranış — ama aynı zamanda sistemin **tazelik sinyali taşımadığının** kanıtı: taşısa, yaş değişimi anlamlı bir değişim olurdu.

### F-11 — Canlı dogfood kanıtı: ölçüm (KRİTİK)

`.metodoloji/blackboard.json` (son mutasyon 20.4 saat önce):

- **11 handoff sinyali**, hepsi `handoff.bmad-check-implementation-readiness` kanalına; yaşlar **0.85 – 1.79 gün**; `consumed: {}` — **kayıtlı tarihte tek bir handshake tamamlanmamış**.
- Board'da **31 adet `E-*` çalışma anahtarı** (`state` tipi), en eskisi **187 saat (7.8 gün)** önce yazılmış; hepsi `"APPROVED: measured=… (GATE-OK-…)"` metni taşıyor.
- `methodology.last_experiment` = `E-051: APPROVED…` ve bu, her oturum başlangıcında "Chain progress" olarak enjekte ediliyor.
- `watchers: {}`, `alerts` içinde 43 saate kadar yaşlı, temizlenmemiş sinyaller.
- **Yorum:** handoff düzlemi pratikte **salt-yazılır bir kuyruk**tur: sinyal gönderiliyor, kimse almıyor, ve 24 saati geçtikten sonra "hung skill" sayılıyor (bkz. `blackboard.py:2461` yorumu: *"Stale (> TTL) stays a health warning: a >24h baton means a crashed/hung skill"*). Yanlış teşhis: sinyalin kendisi otomatik emekliye ayrılmış bir işi duyuruyor olabilir.

### F-12 — (OLUMLU) Kodda zaten iki doğru desen var

- `stop.py:159-180`: `sprint-status.yaml` **bu oturumun başlangıcından eskiyse** yok sayılır (brownfield leftover); *durum-vs-zaman penceresi* karşılaştırması.
- `stop.py:22,102-104,206-226`: yalnızca **bu oturumun** yazıları raporlanır (session marker penceresi).
- `plan.py:175-201`, `guard.py:1030-1051`: `(mtime_ns, size)` tabanlı cache disiplini — **dosya değişirse cache düşer**.
- **Ders:** "durumu bir başka durumla karşılaştır" ve "değişim ölçütünü dosya/commit durumuna bağla" desenleri **kodda zaten var**. Eksik olan tek şey, bu desenin **karar/kod durumu** eksenine taşınmasıdır. Bu, planın en önemli tasarım avantajıdır: yeni bir icat değil, mevcut doktrinin tutarlı uygulanması.

### F-13 — İkinci bir sınıf: kayıt dışı kalıcı durumlar

- `.metodoloji/relay-note.state` (digest + ts) — throttle durumu; tazelikle ilgisi yok ama kalıcı bir "en son ne duyurdum" hafızası (F-10).
- `bmad/workflow/*.state.json` + `events.jsonl` (`bmad/workflow/store.py:12-14`) — çalışma zamanı durumu; `_check_evidence` `artifact`/`command exit 0`/`note` kabul eder (`engine.py:354-421`) ve **evidence'ın ne zaman/kimin tarafından üretildiğine dair kod-durumu bağı yoktur**: bir `artifact` kanıtı, artefakt dosyası değiştikten sonra da "kanıt" sayılmaya devam eder.
- **Sonuç:** freshness açığı yalnız board'da değil; kayıt dışı üç ayrı kalıcı durumda da aynı. **Kapanış (17.10.2026):** `artifact` kanıtı sahiplenildi — E-055 (baseline, measured=1.0) → E-056 (fix, measured=0.0); `command` kanıtı da tarihlendi — E-063 (baseline, measured=1.0) → E-064 (fix, measured=0.0); ayrıntı §13. `relay-note.state` hâlâ işaretli.

### F-14 — Provenance katmanında da üçüncü hâl yok: "doğrulanamadı" yerine "FORGED" (YÜKSEK)

- Bu plan yazılırken koşulan `sh scripts/check-plugin.sh` **11 problem** raporluyor ve `rc=1` dönüyor: `docs/experiments/E-024.md … E-034.md -> approved but --verify failed (FORGED?)`.
- Tek başına `python …/run_experiment.py --verify --record docs/experiments/E-024.md` → `rc=1`, mesaj: *"token … does not match the record's claim/measured/cmd/plan under any of this machine's trusted keys (1 in the ring). If this record was APPROVED on another machine of yours, import that machine's key … Otherwise re-measure it … If it was approved here, the record was tampered with."*
- Yani gerçek durum **"bu makinenin anahtarlarıyla doğrulanamadı"** iken yüzey **"FORGED?" (sahtecilik suçlaması)** basıyor; üçüncü hâl (`UNKNOWN`) provenance katmanında da yok.
- **Sonuç:** C-6 yalnız board/kayıt tazeliğinde değil, **kanıt zincirinin kendi sağlık kontrolünde** de geçerli. 11 kayıt bugün repo'nun kendi sağlık çıktısında kırmızıdır ve arıza **değil**, eksik bir hâldir (makine-yerel anahtar sözleşmesi: README *"FORGED on a teammate's record is normal"*).
- **Ölçüm notu:** bu 11 kayıt, `Re-Measured-By:` satırı eklenmemiş olan kayıtlardır (E-023'ün tarif ettiği deva). Yani bugün "tazelik/emeklilik" işi **elle** yapılıyor ve 11 kayıtta yapılmamış.

---

## 3. Kusur sınıfları (kök nedenler)

Instance'lar farklı, kök nedenler beş tane:

| # | Sınıf | Tanım | Kanıt |
|---|---|---|---|
| **C-1** | **Geçerlilik = yaş varsayımı** | Bir iddianın geçerliliği, yazıldığından bu yana geçen süreye indirgenmiş. TTL hem çok erken öldürür hem çok geç. | F-01, F-11 |
| **C-2** | **İmza ≠ tazelik** | Kanıt *bağlamayı* (kurcalanma), durum *bağlamayla* (geçerlilik) karıştırılmış. Token kayıt-içi olguları kapsar, kod durumunu kapsamaz. | F-04, F-05 |
| **C-3** | **Emeklilik yok → monoton birikim** | Hiçbir kapsam/anahtar/mirror kendiliğinden emekliye ayrılmaz; onaylı kapsam evreni ve `methodology.last_*` sonsuza dek büyür/kalır. | F-03, F-05, F-11 |
| **C-4** | **Deklare-edilmiş-ama-bağlanmamış invalidation** | İnvalidation kavramı isim olarak var, gövdesi yok; hiçbir ölçüm onu kırmızıya çeviremez. | F-06, F-07, F-09 |
| **C-5** | **Okuma yolu denetimsiz, zorlama tembel ve yazma yolunda** | Tazelik yalnızca bir mutasyon olduğunda uygulanır; okuyucular (inject dahil) ham snapshot'ı görür. | F-01, F-02, F-08 |
| **C-6** | **Üçüncü durum yok: `UNKNOWN` temsil edilmiyor** | Sistemde bir iddia ya "geçerli" ya "yok"tur. "Doğrulanamadı" diye bir hâl yok — bu yüzden doğrulanamayan her şey **sessizce geçerli** sayılır. | F-02, F-05, F-13 |

**C-6 en sinsi olanıdır:** tazelik eklenirken "git yok / dosya yok / kayıt eski" durumlarının `STALE` sayılması yanlış alarm üretir; `CURRENT` sayılması ise mevcut hatayı korur. Bu yüzden tasarım üç değerlidir.

---

## 4. Tasarım — Tazelik Düzlemi (Freshness Plane)

### 4.1 İlkeler

1. **Her iddia bir tanık (witness) taşır.** Witness = iddianın *doğru olduğu* kod durumu:
   ```jsonc
   witness = {
     "commit": "<sha|none>",        // iddia yazıldığında HEAD
     "tree":   "<digest|none>",     // kirli work-tree digest'i (index + worktree)
     "scope":  "<digest|none>",     // kapsamla eşleşen dosyaların içerik digest'i
     "ts":     <float>
   }
   ```
2. **Tazelik saklanmaz, türetilir.** (Kanıtlanmış desen: `chain_health` türetir, saklamaz — TD-014'ün tek-kaynak doktrini, `blackboard.py:2036-2057`.)
3. **Üç değerli verdict:** `CURRENT | STALE | UNKNOWN`.
   - `CURRENT`: `witness.commit == HEAD` **ve** `witness.scope == yeniden hesaplanan scope digest`.
   - `STALE`: witness var, kod hareket etmiş **ve** hareket iddianın kapsamını etkilemiş.
   - `UNKNOWN`: witness yok / git yok / dosya yok / digest hesaplanamadı → **geçerli sayılmaz ama bayat da sayılmaz**; yüzey açıkça "doğrulanamadı" der.
4. **Emeklilik birinci sınıf.** "Bu artık geçerli değil" bir *olay* olur: `retire` / `supersede` event'i — `alert-edit`'in kardeşi (`blackboard.py:688-690, 1595`): orijinal `ts` korunur (yaş dürüst kalır), ama **verdict** değişir ve sinyal artık "canlı iş" olarak duyurulmaz.
5. **Tek kaynak, çok yüzey.** Sınıflandırma tek modülde türetilir; `session_start`, `handoffs`, `chain-health`, `doctor`, `guard`, `quality`, `deploy` aynı fonksiyonu çağırır (ikinci bir parser = ikinci bir anlaşmazlık; projenin kendi doktrini).
6. **Mod sistemi, fail-closed doktriniyle.** `[hooks] freshness = "off" | "warn" | "deny"`; brownfield'da `warn`, olgun projede `deny`. `code_guard`'ın yolculuğu (soft → hard, TD-013) emsaldir.
7. **Rapor katmanı, gate'in yerine geçmez.** Tazelik bir *tazelik* ölçer; ölçümü, kaydın imzasını ve zincir kontrolünü **değiştirmez**, yalnız onlara bir boyut ekler.

### 4.2 Nerede yaşar

- **Yeni modül:** `hooks/engine/modules/freshness.py` — saf türetme, fail-open, bounded, tek sorumluluk: `witness_of()`, `classify()`, `scope_digest()`, `retire_marker()`.
- **Board'a yazılan tek şey:** yazım anında **witness event'i** (ve `retire` event'i). Board bir *tazelik* alanı saklamaz; tazelik her okumada türetilir.
- **Kayıt tarafında:** gate, token'ı yazarken `Witness:` satırını da yazar (`Raw Results`/`Metric` satırlarının yanına); `verify()` bu alanı **kayıt gövdesinden yeniden türetir** ve oynanmışsa `FORGED` der — `Final Plan Hash` emsali (`run_experiment.py:1290-1310`).
- **Performans:** digest/HEAD okuması `(HEAD, index mtime, worktree mtime)` ile cache'lenir; `SCOPE_GIT_TIMEOUT_SECONDS` ve `deadline` disiplini (`guard.py:2048-2060`) aynen kullanılır — PreToolUse 10 s bütçesi korunur.

### 4.3 Sınıflandırma kuralları (sözleşme)

| Girdi | Verdict | Yüzey davranışı |
|---|---|---|
| witness yok (legacy kayıt/sinyal) | `UNKNOWN` | enjekte edilir ama "doğrulanamadı" etiketiyle; gate'i etkilemez |
| witness var, `commit == HEAD` ve scope digest eşit | `CURRENT` | bugünkü davranış |
| witness var, HEAD farklı **ve** iddianın kapsamındaki dosyalar değişmiş | `STALE` | enjekte edilir ama "artık geçerli değil olabilir" + değişen dosya sayısı; `freshness=deny` ise guard yazmayı durdurur |
| witness var, HEAD farklı ama kapsam dosyaları değişmemiş | `CURRENT` (kapsam-dar tazelik) | bugünkü davranış — gereksiz alarm yok |
| git/dosya yok | `UNKNOWN` | "doğrulanamadı" |

**Kritik karar:** tazelik **kapsam-duyarlıdır**, commit-duyarlı değil. Kapsam dışı bir commit, kapsam içi bir onayı bayat yapmaz. Bu, yorumun "commit'e tarihle" önerisinin doğru hâlidir — aksi hâlde her commit her onayı bayatlatır ve sistem gürültüye boğulur.

---

## 5. Arayüzler (somut)

### 5.1 Board (`.metodoloji/` event modeli)

```
{"event": "witness", "target": "<key|alert-id>", "commit": "<sha|none>",
 "tree": "<digest|none>", "scope": "<digest|none>", "ts": <float>}
{"event": "retire", "target": "<key|alert-id>", "reason": "<text>", "ts": <float>}
```
- `witness` yazımı `write_key` (`blackboard.py:935-1035`) ve `post_handoff`/`mirror` (`:1580-1650`, `mirror.py:134-180`) içinden **aynı mutasyon kapsamında** (event-first, mevcut doktrin) eklenir.
- `retire` yalnız `ts`'i **değiştirmez** (yaş dürüst kalır), verdict'i değiştirir.

### 5.2 Alert taxonomy

- `AlertKind.CASCADE_INVALIDATION` ve `AlertKind.STALE_SESSION` **artık gerçekten üretilir** (F-06).
- Yeni: `AlertKind.WITNESS_STALE`, `AlertKind.RETIRED`.

### 5.3 Engine yüzeyleri

| Yüzey | Değişiklik |
|---|---|
| `audit.session_start` (`:380-425`) | `waiting_s` ve chain-progress satırlarına verdict + yaş eklenir; `STALE` sinyal "canlı iş" diye duyurulmaz ("artık geçerli değil olabilir — doğrula veya emekliye ayır") |
| `blackboard.pending_handoffs` / `pending_handoff_channels` / `recent_handoffs` | dönen her kayıt `verdict` alanı taşır; `--only-current` filtresi |
| `chain_health` | hop satırlarına `stale`/`unknown` sayacı; `stale` listesi **yaştan değil verdict'ten** doldurulur (yaş korunur, tek başına yetmez) |
| `doctor` | `freshness: {current: n, stale: n, unknown: n}` bloğu; `STALE` artık "hung skill" diye teşhis edilmez |
| `guard` | kapsam kontrolü witness tazeliğine bağlanır (`freshness` moduna göre warn/deny); `_check_scope_coverage`'ın `HEAD`'i artık **iki iş** yapar: değişen dosyalar + tazelik tabanı |
| `quality` / `deploy` | zincir kontrolüne tazelik satırı eklenir (rapor; `deny` modunda blok) |
| `stop` | rapor "bu oturumda yazılanlar"a ek olarak "bu oturumda emekliye ayrılanlar"ı listeler |

### 5.4 CLI (`bmad/scripts/blackboard.py`)

```
handoffs [--skill S] [--freshness]   # verdict + yaş sütunu
chain-health [--freshness]
doctor                               # freshness bloğu
handoff --retire <id|channel> --reason "..."
witness --show <key|alert-id>
```

### 5.5 Config

```toml
[hooks]
freshness = "warn"        # "off" | "warn" | "deny"  (code_guard doktrini)

[freshness]
scope_digest = true       # kapsam digest'i hesapla (kapalıysa yalnız commit)
unknown_policy = "warn"   # "warn" | "silent" | "deny" — UNKNOWN nasıl raporlanır
max_digest_files = 2000   # bounded; üstünde UNKNOWN + neden
```

### 5.6 Kayıt tarafı

```markdown
- **Witness:** commit=<sha|none> tree=<digest|none> scope=<digest|none>
```
- Gate yazar, `verify()` gövdeden yeniden türetir; oynanırsa `FORGED`.
- Legacy kayıtlar (witness'sız) `UNKNOWN` — **`STALE` değil** (yanlış alarm yok).
- `Re-Measured-By:` satırı olan kayıt, yeni kaydın witness'ını **miras alır** → `CASCADE_INVALIDATION` burada üretilir (F-06 gerçekten bağlanır).

---

## 6. Aşamalı plan

> Her aşama **kendi E kaydını** gerektirir (gate doktrini: onaysız kod yok). Aşamalar bağımsız sevk edilebilir ve her biri tek başına değer üretir; sıra bağımlılıkları aşağıda.

### P0 — Ölçüm ve taban (kod yok)

- **Hedef:** arızayı *ölçülebilir* hâle getirmek; mevcut durumda **kırmızı** bir bench.
- **Çıktı:** `docs/experiments/E-052.md` (hipotez aşağıda) + `scripts/bench/bench_freshness.py` (fixture board + fixture git repo).
- **Hipotez (falsifiable):** *"Kapsamı değişmiş bir kayda atıfta bulunan bir handoff sinyali, canlı iş olarak teslim edilir."* Bugünkü ölçüm = **1.0** (arıza mevcut).
- **Yapılacak:** mevcut davranışı sabitleyen testler (bugünkü hâli "doğru" kabul ederek baseline); `pending_handoffs`'ın yaş filtresiz döndürdüğünü kanıtlayan probe; canlı board'dan yukarıdaki 11 sinyalin bir kopyası fixture'a.
- **Kabul:** bench bugünkü kodda `measured=1.0` ve **arıza verdict'i** verir; negtest baseline'ı bozunca kırmızı.

### P1 — `freshness.py` çekirdeği

- **Dosyalar:** yeni `hooks/engine/modules/freshness.py`; `hooks/engine/tests/test_freshness.py`.
- **Çıktı:** `witness_of(root)`, `scope_digest(root, globs)`, `classify(root, witness, scope) -> "CURRENT"|"STALE"|"UNKNOWN"`; git yoksa `UNKNOWN`, patlamaz (fail-open + fail-explicit).
- **Kabul:** üç verdict'in üçü de test edilir; `UNKNOWN`in `CURRENT`a düşmediği negtest'lenir; digest cache `(HEAD, index mtime, worktree mtime)` ile doğrulanır; 1000 dosyalık digest < 300 ms.

### P2 — Okuma yolu (en yüksek fayda / en düşük risk)

- **Dosyalar:** `audit.py` (session_start), `blackboard.py` (`pending_handoffs`, `pending_handoff_channels`, `recent_handoffs`, `chain_health`, `doctor`), `bmad/scripts/blackboard.py` (CLI).
- **Çıktı:** her sinyal/anahtar `verdict` taşır; inject verdict + yaş basar; `STALE` "canlı iş" diye duyurulmaz.
- **Kabul (ölçülebilir):** canlı board'da 11 baton artık `STALE (age 1.8d)` olarak enjekte edilir; enjekte metinde "nobody picked up the baton" ifadesi `STALE` için geçmez; `CURRENT` sinyal için metin **değişmez** (gereksiz gürültü yok).
- **Risk düşük:** salt-okuma; hiçbir davranışı bloke etmez.

### P3 — Emeklilik / retirement protokolü

- **Dosyalar:** `blackboard.py` (`retire` event, `_apply_event`, `handoff-replace` kardeşi), `bmad/scripts/blackboard.py` (`handoff --retire`), `mirror.py`.
- **Çıktı:** bir sinyal/anahtar "artık geçerli değil" olarak işaretlenir; `ts` korunur; `pending_*` onu canlı döndürmez; `retire` event'i replay'de de aynı sonucu verir (drift-free).
- **Cascade:** `Re-Measured-By` ile süpersede olan kayda atıfta bulunan sinyaller `CASCADE_INVALIDATION` ile işaretlenir — **F-06 burada gerçekten bağlanır.**
- **Kabul:** emekli sinyal `pending_handoffs`'ta görünmez; snapshot silinip event log'dan yeniden kurulduğunda **aynı** sonuç; `--negtest` retire event'i bozunca kırmızı.

### P4 — Kayıt tarafı: witness alanı

- **Dosyalar:** `templates/_template_E.md`, `run_experiment.py` (gate yazar + verify türetir), `guard.py` (`find_approved`, `_check_scope_coverage`), `quality`/`deploy` zincir kontrolü.
- **Çıktı:** `Witness:` satırı; verify oynanmışsa `FORGED`; kapsamı değişmiş onay `freshness` moduna göre warn/deny.
- **Kabul:** witness'lı fixture kayıt `CURRENT`; dosya değişince `STALE`; `Witness:` satırı elle değiştirilince `FORGED`; witness'sız legacy kayıt `UNKNOWN` ve **hiçbir projede ani deny üretmez** (brownfield güvenliği).
- **Risk:** gate'in canlı zinciri — bu yüzden P2'den **sonra** (önce rapor, sonra blok).

### P5 — Monoton birleşim düzeltmesi

- **Dosyalar:** `scripts/check-plugin.sh` §3b, `guard._check_scope_coverage`, `custom/config.toml` yorumu.
- **Çıktı:** "scopes in force" artık **taze** kapsamlardır; bayat kapsam `[STALE]` işaretlenir ve `freshness=deny`de yazmayı açmaz. §3b **dogfood'da da** koşar (F-05).
- **Kabul:** kapsamı değişmiş kayıt §3b'de `STALE` satırı üretir; diger tüm dosyalar hâlâ `[OK]`; dogfood'da §3b çalışır ve yeşil/kırmızı verebilir.

### P6 — Hijyen: ölü deklarasyonlar

- **Dosyalar:** `blackboard.py` (`AlertKind`), `guard.py` (`cascade_warnings`, `invalidate_verify_cache`), `blackboard.py:21`, `audit.py:461`, `stop.py:439`, testler (`BLACKBOARD.md §5` atıfları).
- **Kural:** her ölü deklarasyon için **ya bağla ya sil** — ve her silme/bağlama bir **negtest** ile kanıtlanır (projenin kendi negtest doktrini).
- **Kabul:** grep `CASCADE_INVALIDATION|cascade_warnings` artık ya üretici+tüketici gösterir ya da hiç görünmez; `docs/BLACKBOARD.md` atıfları sıfır; negtest'ler kırmızı-verilebilir.
- **Not:** F-09'un temizliği kendi başına önemsiz, ama **sınıfın canlı örneği** olduğu için aynı aşamada kapatılmalı.

### P7 — Self-check: sözleşmeyi türet

- **Dosyalar:** `scripts/check-plugin.sh`, `scripts/check-handoff.py`, `scripts/bench/bench_consistency.py`.
- **Çıktı:** tazelik sözleşmesi **elle bakımsız** doğrulanır: her `witness` tüketicisi (yüzey) ile üreticisi (yazım yolu) arasındaki bağ türetilir; bir yüzey verdict'i düşürürse check kırmızı (E-049 doktrini: *"derives both sets from the tree"*).
- **Kabul:** `check-handoff.py` "yaş filtresiz tüketici" kalırsa kırmızı; yeni bir yüzey eklendiğinde liste elle güncellenmez; `check-plugin.sh` tazelik bölümü `#6h` olarak numaralanır.

---

## 7. Kabul kriterleri (üst düzey)

1. **Canlı kanıt kapanır:** canlı board'daki 11 bayat baton, enjeksiyonda `STALE` olarak ve yaşıyla görünür; hiçbiri "canlı iş" diye duyurulmaz. (Bugün: 11/11 canlı iş olarak duyuruluyor.)
2. **Kapsam-duyarlı tazelik:** kapsam dışı bir commit hiçbir onayı bayatlatmaz; kapsam içi bir değişiklik bayatlatır. İkisi de bench'te kanıtlı.
3. **`UNKNOWN` ayrı bir hâl:** git yok/dosya yok/legacy kayıt → `UNKNOWN`; ne sessiz `CURRENT` ne yanlış `STALE`. Aynı kural **provenance** katmanında da geçerli: anahtarı ring'de olmayan bir kayıt `FORGED` değil `UNVERIFIABLE` raporlanır (F-14; bugün 11 kayıt `FORGED?` diye kırmızı).
4. **Emeklilik replay-güvenli:** snapshot silinip event log'dan kurulunca verdict **aynı**.
5. **Kayıt oynanmaz:** `Witness:` elle değiştirilirse `FORGED`.
6. **Budama/kapsam:** `freshness` türetimi PreToolUse bütçesinin ≤ %10'u; 1000 dosyalık digest < 300 ms.
7. **Her aşama negtest'lenebilir:** en az bir negtest, aşamanın kilit davranışını kırar ve kırmızı verir.
8. **Brownfield güvenliği:** witness'sız bir projede ilk koşu **hiçbir yazma/commit/deploy'u bloke etmez** (`warn`).

---

## 8. Riskler ve sınırlar

- **TOCTOU:** guard'ın yazma-anı kararı ile yazının gerçekleşmesi arasındaki boşluk zaten dokümante bir sınırdır (E-036 *Known Gaps*). Witness da aynı sınırda yaşar; düzeltmez, **ölçer**.
- **git yok / submodule / çok büyük scope:** hepsi `UNKNOWN`; `unknown_policy` ile raporlanır. Fail-closed değil, **fail-explicit**.
- **Performans:** digest maliyeti; cache ve `max_digest_files` sınırı. `guard`'ın mevcut 10 s bütçesi (`SCOPE_GIT_TIMEOUT_SECONDS`) korunur.
- **Yanlış alarm riski:** kapsam-duyarlılık bu yüzden zorunlu; commit-duyarlı bir tasarım her commit'te tüm onayları bayatlatır ve doktrini gürültüye çevirir.
- **Kapsam kayması:** tazelik bir rapor katmanıdır; gate'in yerini almaz, imzayı zayıflatmaz.
- **Ölçüm disiplini:** her aşamanın hipotezi, aşama kodlanmadan **önce** yazılır; "sonra ölçeriz" bu projenin tekrarlayan hatasıdır (E-037) ve bu planda yasak.

---

## 9. Yürütme için gerekli kayıt zinciri

Gate doktrini gereği: **onaysız kod yok.** Bu plan, kayıt olmadan uygulanamaz.

| Aşama | Kayıt | Not |
|---|---|---|
| P0 | **E-052** (research, APPROVED) → `IR-002` → `SP-005` → `S-0xx` | hipotez ölçülür, bugünkü durum kırmızı |
| P1..P5 | her aşama kendi `E-05x` + `QR`; commit'ler `quality`, deploy `PR` | zincir: `E → IR → SP → S → QR → PR` |
| P6 | `TD-016` (ölü deklarasyonlar) + `E-05x` | tech-debt envanteri `check-techdebt.sh` ile uyumlu |
| P7 | `E-05x` | self-check sözleşmesi |

**İlk somut adım (tek cümle):** `E-052 — "Kapsamı değişmiş bir kayda atıfta bulunan handoff sinyali canlı iş olarak teslim edilir"` hipotezini `scripts/bench/bench_freshness.py` ile ölç ve mevcut kodda kırmızı olduğunu kanıtla.

**Gate'e uyum notu:** bu dosya `docs/*.md` (serbest bölge, `FREE_DOC_MD = ^docs/.*\.md$`, `hooks/engine/modules/config.py:333`) altındadır; kod yazımı P1'den itibaren ve yalnız onaylı kapsamda başlar.

---

## 12. Uygulama durumu (P0–P7) — kapanış

Her aşama sevk edildi; ölçüm ve kanıt aşağıda. Tek ölçüm kaydı E-053'tür (uygulama tek bir onaylı
kapsam altında toplandı; kod her yerde bu kayda atıf yapar).

| Aşama | Durum | Sevk edilen artefakt |
|---|---|---|
| **P0** ölçüm/taban | ✅ | `scripts/bench/bench_freshness.py` (fixture git repo + oracle); `docs/experiments/E-052.md` REJECTED, `measured=1.0` — arıza bugünkü kodda kırmızı |
| **P1** `freshness.py` çekirdeği | ✅ | `hooks/engine/modules/freshness.py` — `witness_of` / `scope_digest` / `classify` (CURRENT\|STALE\|UNKNOWN\|RETIRED), batch-scoped cache; `hooks/engine/tests/test_freshness.py` |
| **P2** okuma yolu | ✅ | `audit.session_start` inject'i verdict+yaş basar; `blackboard.pending_handoffs` / `pending_handoff_channels` / `recent_handoffs` / `chain_health` / `doctor` verdict taşır; CLI `handoffs --freshness --only-current`. Bench: `stale_delivery_rate=0.0`, `false_alarm_rate=0.0` |
| **P3** emeklilik | ✅ | `retire` event + `retired_map`, CLI `handoff-retire`; `stop` raporu "bu oturumda emekliye ayrılanlar"ı listeler; `CASCADE_INVALIDATION` `Re-Measured-By`'den türetilir |
| **P4** kayıt-tarafı witness | ✅ | `templates/_template_E.md` + `docs/experiments/_template.md` `Witness:` alanı; `run_experiment.py` yazar + HMAC'e bağlar, `verify()` gövdeden türetir (oynanırsa FORGED); `guard.find_approved` tazelik verdict'ini raporlar/bloke eder |
| **P5** monoton birleşim | ✅ | `scripts/check-plugin.sh` §3b dogfood'da da koşar, bayat kapsamı işaretler; E-053 kapsamı `bmad/scripts/blackboard.py`'yi kapsar (MISS kapandı) |
| **P6** hijyen | ✅ | `cascade_warnings` bağlandı, `invalidate_verify_cache` silindi, `AlertKind` taksonomisi türetilir, `docs/BLACKBOARD.md` atıfları kaldırıldı; `templates/tech-debt.md` ↔ canlı envanter senkron |
| **P7** self-check | ✅ | `scripts/check-handoff.py --freshness-only` (= check-plugin §6h) üretici↔tüketici sözleşmesini ağaçtan türetir: 2 producer / 18 consumer / ham-uyarı okuyucusu yok |

**Ölçülen kabul (E-053):** kapsam-duyarlı tazelik iki yönlü kanıtlı (bayat teslim 0/8, yanlış alarm 0/8);
`UNKNOWN` ayrı bir hâl (legacy kayıt `STALE` değil); emeklilik replay-güvenli (test); `Witness:` oynanırsa
`FORGED` (test); provenance katmanında anahtarı ring'de olmayan kayıt `UNVERIFIABLE` raporlanır (F-14).

**Arayüz notu (§5.4 ↔ sevk edilen CLI):** yetenekler eşleşir, adlar biraz farklı — `handoffs
--freshness [--only-current]`, `witness [--target K]`, `handoff-retire --retire K --reason X`,
`doctor` (freshness bloğu) ve `chain-health` (tazelik bloğunu **varsayılan olarak** basar, ayrı bir
`--freshness` bayrağına gerek yok). `handoff --retire` yerine ayrı bir `handoff-retire` komutu
seçildi (alt-komut ayrıştırıcısı daha net; devam eden testler bu adı kullanır).

**Bilinçli kalan (kapsam dışı, §10):** `bmad/workflow` evidence'ı ve `relay-note.state` işaretlendi, sahiplenilmedi;
F-08'in süresi dolmayan uyarıları P2'de raporlanır ama otomatik eskime yine yaş/Verdict raporu üzerinden yürür.

---

## 10. Bilinçli kapsam dışı

- **Ölçümü yeniden icat etmek yok.** Witness bir *çapa*dır; yeni bir gate, yeni bir metrik dili veya yeni bir onay yolu değil.
- **Yaş bilgisini silmek yok.** Yaş raporlanmaya devam eder (`stale` listesi, `(Nm ago)`), ama geçerlilik ondan **türetilmez**.
- **İmzayı zayıflatmak yok.** `gate_token` aynen kalır; `Witness:` onun yerine geçmez, yanına eklenir.
- **Tüm kalıcı durumlar (F-13) aynı anda düzeltilmez.** `bmad/workflow` kanıt düzleminin `artifact` yarısı sonraki iterasyonda sahiplenildi (§13, E-055 → E-056), `command` yarısı da ondan sonraki iterasyonda tarihlendi (§13, E-063 → E-064: kod-durumu witness'ı, "ayrı bir kayıt gerektirir" diyen bu satırın tarif ettiği şekilde), ve spec'in `artifact` alanı bir sonraki iterasyonda kısıtlandı (§13, E-066 → E-067: run'ın sahibi olmadığı bir yolu ajana teslim etmek de bir ihlaldir). `relay-note.state` (tazelik değil, throttle hafızası) hâlâ **işaretli**, sahiplenilmemiş.

---

## 11. Ek — tek sayfalık teşhis özeti

| Bulgu | Sınıf | Şiddet | Düzeltme |
|---|---|---|---|
| F-01 okuma yolu yaşsız | C-1, C-5 | KRİTİK | P2 |
| F-02 inject bayatı canlı duyuruyor | C-1, C-6 | KRİTİK | P2 |
| F-03 en dayanıklı = en denetlenmemiş | C-3 | YÜKSEK | P2, P4 |
| F-04 imza ≠ tazelik | C-2 | YÜKSEK | P4 |
| F-05 monoton kapsam evreni | C-3 | YÜKSEK | P5 |
| F-06 ölü invalidation düzlemi | C-4 | ORTA | P3, P6 |
| F-07 ölü verify cache | C-4 | ORTA | P6 |
| F-08 süresi dolmayan alert'ler | C-5 | ORTA | P2 |
| F-09 ölü doküman atıfları | C-4 | DÜŞÜK | P6 |
| F-10 throttle yaşı yok sayıyor | C-1 | ORTA | P2 (bilgi) |
| F-11 canlı 11 bayat baton | C-1, C-3 | KRİTİK | P0 → P2 |
| F-14 provenance'da "doğrulanamadı" = "FORGED?" | C-6 | YÜKSEK | P4 |
| F-13 workflow evidence / relay state | C-2 | ORTA | workflow `artifact` kanıtı: **E-055 → E-056**, sertleştirme **E-057 → E-058**, kök kısıtı **E-059 → E-060**, depolama düzlemi kısıtı **E-061 → E-062**, `command` kanıtının tarihlenmesi **E-063 → E-064**, spec'in `artifact` alanının kısıtlanması **E-066 → E-067** ve ad çözümlemesinin kanıt türetmemesi **E-068 → E-069** (§13); `relay-note.state` hâlâ işaretli |

---

## 13. F-13 takibi — workflow kanıt düzlemi (uygulandı, 17.10.2026)

E-053 planı bu sınıfı **işaretlemiş, sahiplenmemişti** (§10): board düzlemi tazelik kazandı, ama `bmad/workflow` kanıtı aynı açığı taşımaya devam etti. Sonraki iterasyonda `artifact` yarısı ölçülüp kapatıldı.

**Arıza (ölçülen):** workflow çekirdeği bir aşamanın kanıtını **yalnızca `complete` anında bir kez** kontrol eder; sonrasında run kaydı aşamayı `completed` sayar ve `next` ondan ileriye hesaplamaya devam eder. Kod durumu ilerlediğinde (artifact silindi, token kaldırıldı, glob eşleşmesi gitti) temel çökmüştür ama ajan onu hâlâ "yapılmış iş" olarak devralır. Bu, F-01/F-02'nin board'daki hâlinin bir kat üstüdür.

**Ölçüm:** `scripts/bench/bench_workflow_evidence.py` — her trial kendi throwaway projesini `create → complete → status/next_stage` ile sürer, iki okuma yüzeyini sınar, oracle modülü import etmez (`pathlib.glob` + `str.count`), 8 stale + 8 current trial, `--baseline` kendi negtest'i.

| Kayıt | Rol | Sonuç |
|---|---|---|
| **E-055** | baseline (fix'siz çekirdek) | `stale_evidence_rate=1.0` — **REJECTED** |
| **E-056** | fix | `stale_evidence_rate=0.0`, `false_alarm_rate=0.0` — **APPROVED + VERIFIED** |

**Tasarım (E-053 doktrininin aynısı, kanıt düzlemine):**

1. **Tamamlanma bir witness taşır:** `artifact` aşaması tamamlanırken onu karşılayan yüklem (`path`, `contains`, `min`) run state'ine yazılır (`state["evidence"][stage_id]`).
2. **Verdict saklanmaz, türetilir:** `bmad/workflow/evidence.py::verify` her okumada artifact'ı yeniden okur.
3. **Üç değerli:** `CURRENT | STALE | UNKNOWN` — witness'sız eski tamamlanma `UNKNOWN` (asla sessiz `CURRENT`), okunamayan artifact `UNKNOWN` (yanlış `STALE` değil).
4. **Yüklem-duyarlı, digest-duyarlı değil:** iddia "bu artifact beyan edilen token'ları taşıyor"dur; token'ları koruyan bir düzenleme `CURRENT` kalır. Digest tabanlı bir tasarım, takip edilen artifact'a sonraki aşamada eklenen her satırı "bayat" sayardı — bench'in eşleştirilmiş yanlış-alarm kontrolü (`rewritten-with-token`, `glob-other-match`) tam olarak bunu reddeder.
5. **Tek türetme, çok yüzey:** `_check_evidence` (tamamlama) ve tüm okuma yüzeyleri aynı eşleştiriciyi çağırır (`evidence.artifact_matches`); `status`/`next` `evidence` bloğu + `stale_evidence` taşır, SessionStart uyarısı `FOUNDATION MOVED: …` basar, `complete` **STALE** bir temele ilerlemeyi reddeder (`--force` geçersiz kılar, `UNKNOWN` asla engellemez).

**Kabul (ölçülen):** bayat kanıt teslimi 0/8; yanlış alarm 0/8; `--baseline` hâlâ 1.0 (bench kendi kırmızısını üretebiliyor); `pytest bmad/tests/test_workflow.py` 119 test yeşil; `check-methodology.sh` yeşil (yalnız E-052/E-055 REJECTED uyarıları, ikisi de bilinçli baseline).

**Kayıt zinciri:** E-055 → E-056 → IR-003 → SP-006 → S-003 → QR-018 (`bmad/tests/test_workflow.py`, `scripts/bench/bench_workflow_evidence.py`).

**Sertleştirme (E-058, 17.10.2026):** aynı düzlemde kalan iki açık ölçüldü ve kapatıldı — E-057 baseline (`stale_evidence_rate=0.3333`, `unbounded_read_rate=1.0`, REJECTED) → E-058 (0.0 / 0.0, APPROVED + VERIFIED). (1) **Envanter yüzeyi** (`list`): operatörün run seçtiği yüzey hiçbir verdict taşımıyordu, yani temeli çökmüş run sağlıklı gibi listeleniyordu → artık `status`/`next` ile aynı türetilmiş bloğu taşır. (2) **Okuma bütçesi**: yeniden türetme oturum kenarında koştuğu halde sınırsızdı (her aday tamamen okunuyor, her glob eşleşmesi taranıyordu) → artık aday başına 4 MiB / 200 aday bütçesi var, bütçe içinde bulunan eşleşme **aynen** cevaplanır ve yalnız negatif taraf gerekçeli `UNKNOWN`'a düşer, yani bütçe asla `STALE` uyduramaz; aşama **tamamlama** okuması sınırsız kalır (operatörün az önce ürettiği artifact bir kez okunur). Bu, E-056'nın "envanter yüzeyi run başına artifact okuması ödememeli" gerekçesiyle açık bıraktığı boşluğu kapatır: bütçe, o okumayı karşılanabilir kılar.

**Sertleştirme 2 (E-060, 17.10.2026):** E-058'in Known Gaps'i ve QR-019'un takip maddesi aynı hazard'ı iki kez **ertelemişti**: "proje kökünün dışına çıkan artifact yolları ne ölçülü ne kısıtlanmış". E-059 bunu ölçtü (`escape_read_rate=1.0`, `escape_accepted_rate=1.0`, REJECTED; üç yazım: `../…`, `docs/../../…`, mutlak yol — üçü de okunuyor ve `complete` kabul ediyordu) ve E-060 kısıtladı (0.0 / 0.0, APPROVED + VERIFIED). Kural tek eşleştiricide (`evidence.root_of` / `_outside`, iki taraf da `realpath` ile çözülür): **beyan edilen** yol kökten çıkıyorsa hiçbir şey okunmadan reddedilir; glob'un **ulaştığı** bir aday (symlink/junction) atlanır ve **sayılır**, böylece atlanan aday yalancı bir `STALE` üretemez — E-058'in okuma bütçesiyle aynı asimetri. İki taraf E-058'deki asimetrisini korur: **gate reddeder** (`complete`, sebebi adlandırarak, witness yazmadan), **view raporlar** (`status`/`next`/`list` `UNKNOWN` der, kökü ve çareyi adlandırır) ve `UNKNOWN` ilerlemeyi asla bloke etmez. Bu, E-056'nın "tek eşleştirici" doktrinini korur: eski bir ağaçtan kalan ya da elle düzenlenmiş bir witness, taze beyan edilmiş bir yolla aynı kısıttan geçer.

**Sertleştirme 3 (E-062, 17.10.2026):** E-060'ın kendi Known Gaps'i "çekirdeğin **kendi depolama** yolları (`store.py`) ve spec'in `artifact` alanı bu kısıtın kapsamı dışında — ayrı bir kayıt" diyordu. E-061 bunu ölçtü ve **E-008'in dikişini bir dosya adı ötede** buldu: `state_path` slug'ı sanitize ederken `load_spec` slug'ı **ham** birleştiriyordu, yani `../../../../loaded` proje kökünün yanındaki bir JSON dosyasını run'ın **iş akışı tanımı** olarak okutabiliyordu; ayrıca state'in kaydettiği `spec_file` nereyi gösterirse orası açılıyordu (eski bir ağaçtan kalan ya da elle düzenlenmiş state — E-060'ın witness için kabul ettiği aynı meşruiyet). Ölçüm: `storage_escape_rate=0.8333` (6 trial'ın 5'i; slug yazımı `status`/`next`'te, kayıtlı yol üç yüzeyde de sızdı — envanter dosya adlarını saydığı ve sanitize edilmiş bir ad ayırıcı taşımadığı için slug yazımından **yapısal olarak** muaf), kontrol 0.0, REJECTED. E-062 (0.0 / 0.0, APPROVED + VERIFIED) düzeltmeyi iki mevcut kuralın eksik yarısı olarak yaptı: (1) sanitizer artık **adlı** bir fonksiyon (`store.safe_slug`) ve `state_path`/`save_spec`/`load_spec` onu kullanır — kuralın ikinci bir kopyası zaten iki yolun ayrı düşme sebebiydi; (2) kapsama sınırı artık **ortak bir modül** (`bmad/workflow/paths.py`), yani kanıt düzlemi ile depolama düzlemi aynı fonksiyonu çağırır (kimlik testle sabitlendi) ve her iki spec adayı da kökün içinde kalır. Çekirdeğin kendi yazdığı hiçbir yol reddedilmez: `save_spec` da aynı sanitizer'dan geçer, geçerli bir `spec_file` yine okunur — eşleştirilmiş kontrol bunu 0.0'da tutar. Sınırlanan `spec_file` okunamazsa cevap dürüstçe "spec yok"tur (`{}`; `complete`: "spec for '<slug>' is missing — cannot advance"), yüzeylerin kapsamayı ayrıca adlandırması ayrı bir kayıt.

**Sertleştirme 4 (E-064, 17.10.2026):** yukarıdaki "bilinçli kalan" listesinin ilk maddesi — `command` kanıtı — ölçüldü ve kapatıldı. E-063 baseline'ı arızayı plandaki tanımdan **daha keskin** buldu: sorun "komutun sonucuna güvenilmemesi" değil, düzlemin o sonuç hakkında **hiçbir şey söylememesi**ydi (`verify`, witness'ı olmayan ve spec türü `artifact` olmayan her tamamlanmış aşamayı atlıyordu). Ölçüm: `command_silence_rate=1.0` (6/6, üç okuma yüzeyi × iki hareket şekli: yeni commit ve kirli takipli düzenleme) ve eşleştirilmiş kontrol de **sessizlikle** düştü (`command_hold_rate=1.0`) — yani "hiç verdict yok" iki yönde de yanlıştı, REJECTED. E-064 (0.0 / 0.0, APPROVED + VERIFIED) tarihler: tamamlanma, komutun koştuğu **takipli kod durumunu** yazar (`code_state`: HEAD'in adlandırdığı commit + takipli çalışma ağacı değişikliğinin digest'i, böylece kirli ağaçta alınan bir tamamlanma hiç görmediği bir commit'e tarihlenmez) ve her okuma onu o anki durumla karşılaştırır. Değişmemiş → **CURRENT** (sessizliğin veremediği gerçek verdict); taşınmış → **UNKNOWN**, iki durumu da adlandırarak; oracle cevap veremiyorsa (git yok, henüz commit yok, `--porcelain=v2` desteklemeyen eski git) → gerekçeli **UNKNOWN**, tahmin değil. **Bir komut asla STALE değildir ve ilerlemeyi asla bloke etmez:** bakmak "bu argv 0 döndü"yü çürütemez, dolayısıyla düzlem karşılaştırabildiğini raporlar ve çürütebiliyormuş gibi yapmaz — `complete`'in ret listesi bu yüzden değişmedi. Oracle board düzleminin zaten kullandığı oracle'dır (E-053/E-054), yani çekirdek ikinci bir "kod taşındı" tanımı icat etmez. Maliyet E-058 usulünce sınırlı: okuma geçişi başına **tek** probe (`CodeStateProbe`), envanter sınıflandırdığı tüm run'lar için bir kez öder ve yalnız artifact witness'ı taşıyan bir proje hiç ödemez. Parmak izi yalnızca **takipli** dosyaları kapsar; her gerekçe metni bunu "tracked" diye söyler.

**Sertleştirme 5 (E-067, 17.10.2026):** önceki iki kapsama düzleminin (E-060 okuma, E-062 çözümleme) **hiç sormadığı** soru ölçüldü: bir yüzeyin *açtığı* dosya değil, **ellettiği** yol. Spec'in üst düzey `artifact` alanı run'ın kalıcı doğruluk kaynağıdır ve iki okuma yüzeyi onu aynen tekrarlıyordu — `status.artifact` ve oturum uyarısındaki `tracked in <yol>`; yani ajanın ilk uyduğu satır. Hiçbir yerde "bu yol run'ın kendi projesinde mi?" diye sorulmuyordu: başka bir projeden kopyalanmış ya da `../` ile yazılmış bir spec, ajana **sahibi olmadığı bir dosyayı** takip etmesini söyleyebiliyordu. Ölçüm: `artifact_field_escape_rate=1.0` (6/6 — üç yazım × iki yüzey) ve `gate` tarafında `artifact_field_accepted_rate=1.0` (3/3, `create` böyle bir spec'i hiç sorgulamadan ekiyordu), eşleştirilmiş kontrol 0.0, REJECTED. E-067 (0.0 / 0.0 / 0.0, APPROVED + VERIFIED) sınırı **yeni bir kural olarak değil, mevcut sınırın üçüncü yüzü** olarak uyguladı: `engine.declared_artifact` işaretçiyi yalnızca `paths.outside` (E-062'nin tek uygulaması) kökün içinde diyorsa teslim eder — içerdeyse aynen, dışarıdaysa `artifact` **None** olur ve `artifact_note` spec'in ne dediğini ve kökü adlandırır; oturum uyarısı da aynı cümleyi kurar (`tracked in …` yerine dışarıda olduğunu söyleyen not); `create` ise böyle bir spec'i **reddeder** (sebebi adlandırarak, hiçbir şey yazmadan). Asimetri E-058'inkiyle aynı: **gate reddeder, view raporlar** — yani eski bir ağaçtan gelen dışarı işaretçili spec run'ı kullanılamaz hale getirmez, yüzeyler durumu ve çareyi adlandırır. Bu, §13'te iki kez "bilinçli kalan" diye yazılmış maddenin kapanışıdır.

**Sertleştirme 6 (E-069, 17.10.2026):** §13'ün "`_resolve_slug` bir slug sorusuna cevap vermek için tüm run'ları sınıflandırmaya devam eder" diye işaretlediği maliyet ölçüldü ve kaldırıldı. Arıza bir DOĞRULUK değil **gereksiz iş** arızasıydı: CLI'ın en sık kullanımı `--slug` taşımaz, ve çözümleyici "hangi run?" sorusunu **envanter** üzerinden cevaplıyordu — envanter ise her run için bir kanıt verdict'i türetir (E-058'in sınırlı artefakt okumaları, E-064'ün paylaşılan kod-durumu probe'u). Yani bir dosya adı sorusu bütün kanıt düzlemini çalıştırıyordu; tek run'lı bir projede çözümleme o run'ı sınıflandırıp `status` **ikinci kez** sınıflandırıyordu, çok run'lı bir projede ise yalnızca "adres belirsiz" olduğunu öğrenmek için hepsini. Ölçüm: `slug_resolve_read_rate=1.0` (2/2 trial), kontrol 0.0, REJECTED. E-069 (0.0 / 0.0, APPROVED + VERIFIED) kuralı düzlemin kendi cümlesi olarak uyguladı — **bir yüzey sorusunun ihtiyaç duymadığı şeyi hesaplamamalı** — ve çözümlemeyi `engine.resolve_slug`'a taşıdı: cevap `store.list_slugs`'ın listelediği adlardan gelir (her state dosyası için bir ad, bozuk olanlar dahil), yani "0 / 1 / çok" cevabı envanterinkiyle **yapısal olarak** aynıdır, yalnızca verdict'ler düşer; `status` cevapladığı run'ı tam bir kez sınıflandırır. CLI'daki `_resolve_slug` ince bir delege olarak kaldı, böylece red metni ve çıkış kodu değişmedi. E-058 açısından ders şudur: bir okumayı **bütçelemek** onu karşılanabilir kılar, **sorgulamak** ise bedava — ve bu okuma üç iterasyon boyunca bütçeli olduğu için bitmiş görünmüştü.

**Bilinçli kalan (hâlâ işaretli):** `relay-note.state` tazelik değil throttle hafızasıdır; `history` sınıflandırılmaz (geçiş izi workspace hakkında bir iddia değildir); workflow tamamlanmasının blackboard aynası verdict taşımaz (yazma anında taze olduğu için orada gözlemlenebilir bir boşluk yoktur); kökün dışına bakan bir `specs/` dizini (aynı kontrol onu da reddeder) fixture ile ölçülmedi; çözümleme ile okuma arasındaki bir yarış (link'in arada değişmesi) kapatılmadı — düzlem yalnızca kurabildiğini raporlar; `command` tarafında kalan üç sınır E-064'ün Known Gaps'inde: takipsiz dosya değişikliği parmak izinin dışında, probe'un 5 s tavanı beyan edilmiş ama zamanlanmamış, ve git ağacı olmayan bir proje bir komut tamamlanması için asla CURRENT diyemez.
