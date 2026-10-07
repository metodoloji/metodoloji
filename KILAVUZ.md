# AGENT RUNBOOK — metodoloji (v0.1.0)

---

## İçindekiler

1. [Bu Runbook'u Nasıl Kullanırsın](#0-bu-runbooku-nasıl-kullanırsın)
2. [Operasyon Sözleşmesi — Değişmezler](#1-operasyon-sözleşmesi--değişmezler)
3. [Zihinsel Model — Sistemi 60 Saniyede Anla](#2-zihinsel-model--sistemi-60-saniyede-anla)
4. [Karar Ağacı — Kullanıcı Talebini Playbook'a Bağla](#3-karar-ağacı--kullanıcı-talebini-playbooka-bağla)
5. [P0 — Oturum Açılışı (her oturum)](#4-p0--oturum-açılışı-her-oturum)
6. [P1–P6 — Kurulum ve Başlangıç Playbook'ları](#5-p1p6--kurulum-ve-başlangıç-playbookları)
7. [P7–P18 — Teslim Zinciri Playbook'ları](#6-p7p18--teslim-zinciri-playbookları)
8. [P19–P27 — Sürdürme Playbook'ları](#7-p19p27--sürdürme-playbookları)
9. [Kayıt Zinciri Referansı E → IR → SP → S → QR → PR](#8-kayıt-zinciri-referansı-e--ir--sp--s--qr--pr)
10. [Hook Motoru ve Mekanik Kapılar](#9-hook-motoru-ve-mekanik-kapılar)
11. [Free Zone / Korumalı Alan / Secret Taraması](#10-free-zone--korumalı-alan--secret-taraması)
12. [Güvenlik — Gate Key, Güven Halkası ve HMAC](#11-güvenlik--gate-key-güven-halkası-ve-hmac)
13. [Skill Kataloğu — Hangi Skill'i Ne Zaman Çağırırsın](#12-skill-kataloğu--hangi-skilli-ne-zaman-çağırırsın)
14. [TOML Customization — 3 Katman + 8 Katman Config](#13-toml-customization--3-katman--8-katman-config)
15. [Blackboard — Çalışma Bağlamı](#14-blackboard--çalışma-bağlamı)
16. [Komut Referansı](#15-komut-referansı)
17. [Denetim ve Sağlık Kontrolü](#16-denetim-ve-sağlık-kontrolü)
18. [Hata Ayıklama — Semptom → Senin Aksiyonun](#17-hata-ayıklama--semptom--senin-aksiyonun)
19. [ASLA Listesi — Atlanırsa Can Yakan 60 Detay](#18-asla-listesi--atlanırsa-can-yakan-60-detay)
20. [Kullanıcıya Raporlama Sözleşmesi](#19-kullanıcıya-raporlama-sözleşmesi)
21. [Referans — Mimari Harita, Kök Çözümleme, Sözlük](#20-referans--mimari-harita-kök-çözümleme-sözlük)

---

## 0. Bu Runbook'u Nasıl Kullanırsın

Bu dokümanı baştan sona "okuyup bitirilecek" bir metin sanma. Bir **karar tablosudur**: durumu tanı, ilgili bölüme git, talimatı uygula.


| Durumun                                                | Git                                                     |
| ------------------------------------------------------ | ------------------------------------------------------- |
| Hedef projede ilk kez çalışıyorsun                     | #4 (P0 oturum açılışı) → #5 (P1: init/gate-setup/audit) |
| Kullanıcı bir şey inşa etmeni istedi                   | #3 (karar ağacı) → #6 (ilgili P7–P18)                   |
| Kullanıcı "neden bloklandı / neden geçmedi" diye sordu | #17 (hata ayıklama) → #19 (nasıl raporlarsın)           |
| Zincir/commit/deploy reddedildi                        | #17 → #8 (kayıt alanları) → #9 (hangi kapı, hangi mod)  |
| Kullanıcı kayıt/plan/customization istedi              | #8, #13, #14                                            |
| Kullanıcı "her şeyi denetle" dedi                      | #16 (denetim)                                           |
| Bir kuralı ihlal etmek üzere olduğunu düşünüyorsun     | #1 (değişmezler) + #18 (60 madde)                       |


**Okuma disiplini — ezberden değil kaynaktan:**

1. Zincirin gerçek durumunu **SessionStart çıktısından** al (`Chain progress`, kayıt envanteri, sprint satırı, PROACTIVE dürtme). Modelin ezberi değil, oturumun kendi durumu esastır.
2. `{metodoloji-root}` değerini uydurma: SessionStart'ın `METODOLOJI active (plugin: PATH)` satırını **verbatim** kullan. Bulamıyorsan #20'deki çözümleme sırasına bak.
3. Bir script'in arayüzünü hatırlamıyorsan `--help` koş (`run_experiment.py --help`, `blackboard.py --help`, `workflow.py --help`). Motor ve CLI'lar kanoniktir; bu doküman onların özetidir.
4. Bu doküman v0.1.0 içindir. Şüphede çapraz kontrol: `docs/CLAUDE.md` (motor özeti), `docs/bmad/*-methodology.md` (manifesto), `templates/` (güncel şablon), `.plugin/plugin.json` (sürüm).

---

## 1. Operasyon Sözleşmesi — Değişmezler

Bunlar tercih değil, sistemin çalışma koşulu. İhlali ya mekanik olarak reddedilir (deny) ya da sessizce kanıt üretir — ikisi de kabul edilemez.

1. **Koda giden tek yol bir VERIFIED deneydir.** `docs/experiments/E-NNN.md` kaydın `APPROVED` + gerçek `GATE-OK-...` token'ı yoksa kod yazma. "Küçük bir değişiklik" istisna değildir.
2. **Ölçümü gate koşar, sen beyan etmezsin.** Sayıyı, metriği, kararı sen yazmazsın; gate kendi koştuğu çıktıdan türetir.
3. **Gate-yazımlı alanlara dokunmazsın.** `Decision`, `Gate Evidence`, `Next Step`, `Raw Results`, `Uncertainty`, `Metric`, `Measurement Command`, `Status` satırları. Elle düzeltme = token bozulur = FORGED.
4. **Çıktının kökünü "ne olduğu" belirler.** Kayıt/artefakt → `{project-root}`; template/config/script → `{metodoloji-root}`. Tek istisna `bmad-customize` (plugin `custom/`'ına yazar).
5. **Gate key'e dokunmazsın.** `~/.bmad/gate-key` repo dışıdır; içeriğini basmaz, kopyalamaz, `cat`'lemez, paylaşmaz, üzerine yazmazsın. Anahtar sorunu `/metodoloji:gate-setup` işidir, shell işi değil.
6. **Free zone güvenlik deliği değildir.** Sekret taraması free-zone kontrolünden **önce** koşar; `scratch/` bile `gate-key`/`.bmad`/`gate_token` kalıplarında DENY yer.
7. **Bench korumalı dizinde durur** (`scripts/bench/`). `scratch/`, `tmp/`, `temp/`, `graft/`, `openhands/`, `_bmad/`, `.metodoloji/` ve kök `explore_*` free yüzeylerinden gate ölçüm koşmaz.
8. **Bir deny'i "aşmanın" yolu kaydı tamamlamaktır** — dosyayı başka araçla yazmak, `$var` ile gizlemek, modu düşürmek, token'ı elle yeniden üretmek değil. Guard shell yazımlarını da görür.
9. **Sırayı sen seçmiyorsun.** Zinciri (E→IR→SP→S→QR→PR) ve beyan edilen workflow'ların sıradaki aşamasını çekirdek hesaplar; sen aşamanın **işini** yaparsın.
10. **`FORGED`, karşı makinede normaldir.** Anahtar makine-yereldir; yabancı imza provenans bilgisidir. Çözüm #6 P11'dedir — token'ı elle onarmak değil.
11. **Stop kapısı seni asla engellemez** (report-only). Oturum kapanışını bir deny'e bağlamak yanlış teşhistir (#17).
12. **İş bitmeden rapor "bitti" demez.** Soft modda uyarıyla geçtiysen bunu söylersin; hard modda aynı eksik deny olur (#19).

---

## 2. Zihinsel Model — Sistemi 60 Saniyede Anla

1. **Kod yazmak izne bağlıdır.** İzin = `docs/experiments/E-NNN.md` kaydının gate'i çalıştırıp `APPROVED` + `GATE-OK-...` token üretmesi.
2. **Zincir:** `E (deney) → IR (hazır mıyız?) → SP (sprint planı) → S (story) → QR (kalite) → PR (prod hazırlık)`. Her halka bir sonrakini açar.
3. **Bekçiler (hook'lar):** Her dosya yazma / commit / deploy girişiminde motor sorar: "kapsayan VERIFIED deney var mı? zincir tamam mı?" Yoksa `DENY` (hard) veya uyarı (soft).
4. **Çıktı hep proje köküne:** Metodoloji *çıktısı* (story, deney, planlama/test artefaktları — hepsi `docs/` altında) asla plugin içine yazılmaz. Plugin *kaynağı* (template, TOML, script) plugin kökünden okunur.
5. **İstisna tek:** `bmad-customize` kullanıcı override'larını plugin'in `custom/` altına yazar. Karar kuralı: *dosya neyse oraya gider* (kayıt/artefakt → `{project-root}`, template/config/script → `{metodoloji-root}`).
6. **Anahtar makine-yereldir, güven halka senin:** `~/.bmad/gate-key` asla repo'ya girmez, paylaşılmaz. İmza tek anahtarla: senin makinendeki. Doğrulama halkaya bakar: kendi anahtarın + `--import-key` ile `~/.bmad/gate-keys/` altına aktardığın eş makine anahtarları — kendi makinelerinden birinde imzalanmış kayıt hepsinde doğrulanır. Halka dışı bir anahtarın kaydı `FORGED` verir — bu tasarım, saldırı değil.

```
Kullanıcı isteği → E kaydı → gate çalıştır → APPROVED → kod yaz (scope içinde)
                 → IR → SP → S → kodla → QR → commit → PR → deploy → rapor
```

---

## 3. Karar Ağacı — Kullanıcı Talebini Playbook'a Bağla

Kullanıcı niyeti çoğu zaman açık değildir; **isteği playbook'a sen bağlarsın ve ne yapacağını bir cümleyle söylersin** (devam etme izni istemek için değil, yanlış playbook'u koşmamak için).


| Kullanıcının dediği (temsili)               | Playbook                                           |
| ------------------------------------------- | -------------------------------------------------- |
| "Bu projede sistemi kur / başlat"           | #5 P1 → (yeni proje) P2 / (mevcut repo) P3         |
| "Sıfırdan şu uygulamayı yaz"                | #6 P8 (ana zincir) — P2 kurulumun üstüne           |
| "Mevcut projeye metodoloji giydir"          | #5 P3 → #7 P19 (ilk VERIFIED'dan sonra sıkılaştır) |
| "Pluginin kendisini geliştir"               | #5 P4                                              |
| "Sadece hızlı bir deneme/prototip"          | #5 P5 (scratch — gatesiz)                          |
| "Ekip olarak / başka makinemde de çalışsın" | #5 P6                                              |
| "Şu feature'ı ekle"                         | #6 P7 (tam akış)                                   |
| "Gate reddetti / APPROVED olmadı"           | #6 P9 (REJECTED) / P10 (ADVISORY-BLOCK)            |
| "Bu kayıt FORGED görünüyor"                 | #6 P11                                             |
| "Story yazarken takıldım"                   | #6 P12                                             |
| "Sprint taştı / story blocked"              | #6 P13                                             |
| "Kod yazarken yeni bir soru çıktı"          | #6 P14                                             |
| "Bugfix/hotfix gerekiyor"                   | #6 P15                                             |
| "Bu borcu kaydet"                           | #6 P16                                             |
| "Prod'da incident oldu"                     | #6 P17                                             |
| "İki işi paralel yürütelim"                 | #6 P18                                             |
| "Soft moddan çık, sıkılaştır"               | #7 P19                                             |
| "Commit/deploy'u geçiremiyorum"             | #7 P20                                             |
| "Oturumda ne oldu / log'a bak"              | #7 P21                                             |
| "Her şeyi denetle"                          | #7 P22 → #16                                       |
| "Kimin sırası / hangi handoff bekliyor"     | #7 P23 → #14                                       |
| "Takım kuralı koyalım"                      | #7 P24 → #13                                       |
| "Plugini güncelle"                          | #7 P25                                             |
| "`.env` güvenli mi"                         | #7 P26                                             |
| "Şu süreci (workflow) koştur"               | #7 P27                                             |
| "Plugin sağlığı / ne bozuk"                 | #16 + #17                                          |


**Karar ağacı — nereden başlayacağını bilmiyorsan:**

```
Hedef projede .metodoloji/initialized var mı?
├── YOK
│   ├── Proje boş mu?            → P1 → P2 (greenfield)
│   ├── Proje dolu (kod var)?    → P1 → P3 (brownfield)
│   └── Pluginin kendi reposu mu?→ P1 → P4 (self-hosting)
└── VAR
    ├── Kullanıcı sadece deneme istiyor   → P5 (scratch, gatesiz)
    ├── Üretim işi var                    → P7 (feature akışı) / P8 (fikirden)
    └── Sadece bakım/denetim istiyor      → P22 (denetim) + P19 (mod sıkılığı)
```

---

## 4. P0 — Oturum Açılışı (her oturum)

**Tetikleyici:** Her yeni oturum. Kullanıcı henüz bir şey istemeden koş.

**Yap:**

1. **SessionStart bağlamını oku** (motor otomatik basar): zincir hatırlatıcı + gate key durumu + init durumu + **proje durumu** (kayıt envanteri + sprint satırı: review/in-progress story'ler, epic-lag bayrakları) + bekleyen handoff varsa PROACTIVE dürtme + board'da E/IR/SP/S/QR/PR run key varsa zincir ilerlemesi (`Chain progress`). Oturumu bu satırlarla aç, ezberle değil.
2. **`METODOLOJI active (plugin: PATH)` satırını not et** — `{metodoloji-root}` bu; verbatim kullan, arama yapma (#20).
3. **Durum yoklaması:** bekleyen handoff (`handoffs`), açık run list, `hot` anahtarı (#14). Kullanıcıya "nerede kaldık" özetini bir cümlede ver.
4. **Uyarıları yorumla:** "marker var → init'i tekrar çalıştırma", "marker yok → bir kez çalıştır", iskelet var + marker yok → **bozuk init** (tek seferlik onarım: `python3 {metodoloji-root}/bmad/scripts/skeleton.py --install`).
5. **Kullanıcı isteğini al** → #3 karar ağacına bağla ve hangi playbook'u koşacağını söyle.

**Yapma:** Oturum başında toplu denetim koşma (`check-plugin.sh` \~65s sürer) — kullanıcı istemedikçe P22'ye bırak.

### 4.1 Rutin checklist (kendine uygula)

**Her oturum başı:**

- [ ] `/metodoloji:audit` veya en azından `git status` + bekleyen handoff (`handoffs`) kontrolü
- [ ] Bugünün story'sinin E/IR/SP bağı sağlam mı (`--verify` yeşil mi?)
- [ ] Blackboard odağı (`write --hot`) + run list açık mı?

**Her kod öncesi:**

- [ ] Hedef dosya VERIFIED scope içinde mi?
- [ ] Bench korumalı dizinde mi? `--dry-run` görüldü mü?

**Her commit öncesi:**

- [ ] AC 4'lüsü + task↔AC + DoD-NNN tamam mı?
- [ ] Done story'nin QR'ı `docs/quality/`'de mi? Tablolar standart formatta mı?
- [ ] IR/SP referansları mevcut mu?

**Her deploy öncesi:**

- [ ] PR: staging PASS, rollback testli, alertler kurulu, kill switch var, pencere+onay tamam mı?

**Haftalık:**

- [ ] `pytest` + 4 statik check yeşil mi (`check-custom`, `check-plugin`, `check-methodology`, `check-techdebt` + `check-handoff.py`)?
- [ ] Tech-debt tablosu güncel mi? P0/P1'e hedef sprint var mı?
- [ ] `chain-health` + `doctor --json` temiz mi? Açık handoff'lar tüketildi mi?
- [ ] `.env` hijyeni (#6a) + audit log'da `methodology_warnings` taraması

---

## 5. P1–P6 — Kurulum ve Başlangıç Playbook'ları

### P1 — İlk kurulum: `init` → `gate-setup` → `audit`

**Tetikleyici:** "Sistemi kur", "başlat", ya da hedef projede `.metodoloji/initialized` yok.

**Sıra kesinlikle şudur:** `init` → `gate-setup` → `audit` → ilk E kaydı. Sırayı değiştirme, adım atlama.

**Adım 0 — Önkoşul kontrolü (offline, kimlik istemez):**


| Gereksinim              | Minimum    | Not                                                                |
| ----------------------- | ---------- | ------------------------------------------------------------------ |
| Python                  | 3.11+      | `tomllib` için şart; motor `python3 → python → py` sırasıyla dener |
| OpenHands / Claude Code | Güncel     | Plugin/hook API desteği                                            |
| Git                     | 2.x        | Versiyon kontrol                                                   |
| Shell                   | POSIX `sh` | Windows'ta Git Bash yeter                                          |


Windows trikleri: bootstrap Python'u otomatik bulur, `cygpath` varsa yolu çevirir, `chmod 600` sessizce yoksayılır, `.sh` dosyaları `.gitattributes` ile LF'e normalize edilir.

**Adım 1 — Kurulum yolu (kullanıcının runtime'ına göre birini seç):**

OpenHands (SDK):

```python
from openhands.sdk.plugin import install_plugin
install_plugin("github:yunusgungor/metodoloji")
# → ~/.openhands/plugins/installed/metodoloji/
```

Geçici deneme (lokal yol dahil):

```python
from openhands.sdk.plugin import Plugin
p = Plugin.load(Plugin.fetch("github:metodoloji/metodoloji"))
p = Plugin.load("/path/to/metodoloji")  # lokal repo kökü
```

Claude Code (marketplace):

```bash
/plugin marketplace add https://github.com/metodoloji/metodoloji
/plugin install metodoloji@metodoloji
claude plugin enable metodoloji
```

> Plugin **opt-in**'dir (`defaultEnabled: false`). Fail-closed guard hook'ları açıkça enable edilmeden çalışmaz.

Manuel kurulum (doğrulama):

```bash
git clone https://github.com/metodoloji/metodoloji.git
cd metodoloji
ls .plugin/plugin.json
ls hooks/hooks.json
ls hooks/engine/main.py
ls .claude-plugin/marketplace.json
python3 --version  # >= 3.11 olmalı
```

**Adım 2 — SessionStart'ın kendiliğinden yaptığı işi bil (tekrar etme):** `bootstrap.sh` (fail-open):

1. `~/.bmad/gate-key` yoksa oluşturur (0600, POSIX'te).
2. `.metodoloji/logs/` dizinini açar (runtime, her oturumda gerekli). `docs/experiments/` **yalnızca init marker'ı yoksa** açılır — init bir kez yapılan bir iştir.
3. Bağlam enjekte eder: zincir hatırlatıcı + gate key durumu + init durumu + proje durumu + bekleyen handoff dürtmesi + `Chain progress` (bkz. #4).

**Adım 3 — `/metodoloji:init` (proje başına BİR kez):**


| İş           | Detay                                                                                                                                                                                                                             |
| ------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Dizinler     | `docs/experiments/`, `docs/development/stories/`, `docs/quality/` (QR'ın kanonik evi), `docs/research/`, `docs/design/`, `docs/design/prds/`, `docs/design/ux-designs/`, `docs/design/architecture/`, `scratch/` — varsa dokunmaz |
| Templateler  | E, BD, C, IR, SP, QR, PR, S + README + tech-debt + scratch-README — **üzerine yazmaz**, mevcutu korur                                                                                                                             |
| Marker       | `.metodoloji/initialized` yazar (`initialized_at` + `plugin_version`). Sonraki çağrılarda marker varsa init **kısa devre** yapar: hiçbir dizin/şablon işlemi tekrarlanmaz. Yeniden kurmak için `--force` ya da marker'ı sil.      |
| Manifestolar | Kopyalamaz (plugin-kanonik)                                                                                                                                                                                                       |
| Uyarı        | Gate key yoksa `/metodoloji:gate-setup`'a yönlendirir                                                                                                                                                                             |


**Adım 4 — `/metodoloji:gate-setup` (makine başına BİR kez):**

```bash
python3 {metodoloji-root}/skills/bmad-research-experiment/scripts/run_experiment.py --init-secret
python3 {metodoloji-root}/skills/bmad-research-experiment/scripts/run_experiment.py --check-secret  # oluşturmadan kontrol
```

Kurallar: dosya repo **dışında**, 0600, `secrets.token_hex(32)` ile üretilir, varsa **asla üzerine yazılmaz** (eski kanıtlar bozulur), içeriği **asla ekrana basılmaz/kopyalanmaz**.

> **Windows + WSL:** PowerShell evi (`C:\Users\<sen>\.bmad\gate-key`) ile WSL evi (`~/.bmad/gate-key`) farklıdır ama metodoloji ikisini tek makine sayar — anahtar tek tarafta varsa diğer tarafta tüm token'lar `FORGED` görünür (#0/#3 patlar). Anahtarı **bir kez** üretip diğer tarafa senkronla (`cp /mnt/c/Users/<sen>/.bmad/gate-key ~/.bmad/gate-key && chmod 600 ...`), iki kez `--init-secret` çalıştırma. Yenilersen hemen re-sync.

**Adım 5 — `/metodoloji:audit`:**

```bash
sh scripts/check-plugin.sh
```

Kapsam ve # haritası #16'dadır. Exit 0 = HEALTHY; sorun varsa #17'ye geç.

**Adım 6 — Kurulum doğrulaması (hepsi offline):**

```bash
python -m pytest -q             # 1100+ test: hook motoru, bridge, skill
sh scripts/check-custom.sh      # bridge TOML statik denetimi
sh scripts/check-plugin.sh      # plugin yapı denetimi (#0–#6f)
sh scripts/check-methodology.sh # kayıt format denetimi
```

CI notu: repo kökünde `.github/workflows/` yoktur — "her push'ta CI koşar" varsayma. Değişiklikten sonra 6 kontrolü yerelde koş (pytest + 4 statik check + handoff lint).

### P2 — Greenfield (boş klasör, önerilen yol)

**Tetikleyici:** Kullanıcı yeni bir proje başlatıyor, klasör boş.

```bash
# 0. Klasör + git
mkdir benim-projem && cd benim-projem && git init

# 1. Plugin kur (Claude): /plugin marketplace add ... + install + enable
#    veya OpenHands: install_plugin(...)

# 2. İskelet + anahtar + denetim (init proje başına BİR kez)
/metodoloji:init          # marker yazar; tekrar çağrılırsa no-op
/metodoloji:gate-setup
/metodoloji:audit        # veya: sh {metodoloji-root}/scripts/check-plugin.sh

# 3. Sert moda geç (greenfield'da bekleme! brownfield-soft sana değil)
# custom/config.toml [hooks]: code_guard="hard", quality_gate="hard", deploy_guard="hard"
# stop_guard zaten report-only — anahtar dursa da etkisi yok.

# 4. İlk deneyi yaz (DAR scope — trik: ilk scope'u bilerek küçük tut)
cp {metodoloji-root}/templates/_template_E.md docs/experiments/E-001.md
# E-001.md'yi doldur: Theory/Hypothesis/Metrics/Design/Code Scope (örn. src/auth/**)
mkdir -p scripts/bench
# ... scripts/bench/bench_auth.py yaz (BU bench korumalı dizinde — free zone'a koyma!)
python3 {metodoloji-root}/skills/bmad-research-experiment/scripts/run_experiment.py \
  --record docs/experiments/E-001.md --run "python scripts/bench/bench_auth.py" --dry-run
python3 {metodoloji-root}/skills/bmad-research-experiment/scripts/run_experiment.py \
  --record docs/experiments/E-001.md --run "python scripts/bench/bench_auth.py"
python3 {metodoloji-root}/skills/bmad-research-experiment/scripts/run_experiment.py \
  --verify --record docs/experiments/E-001.md   # VERIFIED bekle

# 5. Zinciri kur: IR → SP → S
cp {metodoloji-root}/templates/_template_IR.md docs/development/IR-001.md   # Status: READY
cp {metodoloji-root}/templates/_template_SP.md docs/development/SP-001.md   # hedef tek cümle + S listesi
cp {metodoloji-root}/templates/_template_S.md docs/development/stories/S-001.md
# S-001 frontmatter experiment_refs + AC(*) + Task(AC:..) + DoD(DoD-NNN) doldur

# 6. Kodla (guard scope'u açık) → QR → commit → PR → deploy
# ... src/auth/** altında kod yaz
cp {metodoloji-root}/templates/_template_QR.md docs/quality/QR-001.md
git commit -m "feat(auth): S-001 ..."     # quality gate IR→QR→SP kontrol eder
cp {metodoloji-root}/templates/_template_PR.md docs/development/PR-001.md
# ... deploy komutu → deploy gate IR→QR→SP→PR kontrol eder
```

**P2 kuralları:** İlk E'yi "dünyayı kapsayan" değil "tek modülü kapsayan" yaz (guard scope dar = blast radius küçük). Bench'i koddan ÖNCE korumalı dizine koy (sonradan taşımak `Measurement Command` bağını bozar — aslında yeni kayıt gerekir). `--dry-run`'sız `--run` koşma.

### P3 — Brownfield (mevcut projeye metodoloji giydirme)

**Tetikleyici:** Çalışan kod var, deney geçmişi yok. **Direkt hard mod = her yazım DENY = kilit.**

```bash
cd mevcut-proje
/metodoloji:init          # BİR kez; mevcut dosyaları ezmez — güvenli; marker yazar
/metodoloji:gate-setup
# custom/config.toml [hooks] → SADECE code_guard="soft"
# (UYARI: bu dosya plugin-global politikadır — tüm projeleri etkiler; geçici yumuşat,
# ilk VERIFIED scope'ta "hard"a geri al. quality_gate/deploy_guard'a DOKUNMA —
# onlar commit/deploy bekçisidir; stop_guard zaten "soft" ve hiçbir şey onu okumaz.)
# Bu "uyarı + geç" modunda çalışmaya başla.
/metodoloji:audit
```

Sonra **kapsama haritası** çıkar: en aktif modülü seç (örn. `src/billing/**`), ona DAR bir E-001 yaz, bench'i `scripts/bench/`'e koy, APPROVED al, `--verify` ile VERIFIED gör. İlk VERIFIED scope doğar doğmaz:

```toml
[hooks]
code_guard = "hard"     # sıkılaştır
quality_gate = "hard"
deploy_guard = "hard"
```

**P3 kuralları:** Tüm codebase'i tek E ile açmaya çalışma (scope ne kadar genişse kanıt o kadar zayıf + FORGED riski o kadar büyük). Modül modül ilerle: her modüle bir E. `stop`'un stale `sprint-status.yaml`'ı yoksayması brownfield artıkları için bilerek var — eski status dosyan oturum başından eskiyse stop onu raporlamaz, panikleme. Legacy `docs/development/QR-NNN.md` kayıtların varsa taşıma — kabul ediliyor; ama YENİ QR'ları `docs/quality/`'ye aç.

### P4 — Self-hosting (pluginin kendi reposunda çalışma)

**Tetikleyici:** `metodoloji` reposunun içinde kod değiştiriyorsun (motor, skill, template).

Koşullu self-modifikasyon zonu devrede: korunan proje kökü pluginin kendi reposuysa `hooks/`, `scripts/`, `skills/`, `custom/` serbesttir. Başka projede bu ağaçlara dokunmak deney ister.

**Ek kurallar:** `hooks.json`'u elle editleme — `scripts/sync-hooks-json.py --write` ile üret (`check-plugin.sh` #1b byte-identical ister). Değişiklikten sonra `python -m pytest -q` + statik check'leri koş. Davranış değişikliğini E kaydıyla belgele (bench + E — #18'deki improvement loop).

### P5 — Solo-spike (tek kişilik hızlı deneme)

**Tetikleyici:** "Acaba şu kütüphane işimi görür mü?" — kullanıcı kayıt zinciri kurmak istemiyor.

`scratch/`'a yaz — gatesiz, serbest. Bench denemelerini scratch'te pişir, stabilize olanı `scripts/bench/`'e **terfi ettir** (gate free zone'dan bench koşmaz). Secret kalıplarının scratch'te bile DENY verdiğini unutma (`gate-key`, `gate_token`...). Prototip üretime gidecekse P2'ye dön: E aç, ölç, scope'u aç, kodu taşı.

### P6 — Ekip + çok makine

**Tetikleyici:** 2+ geliştirici, herkes kendi makinesinde.

Herkes kendi anahtarını üretir (`gate-setup` — paylaşmak YASAK). Ali'nin APPROVED kaydı Ayşe'de `FORGED` verir — normal. Akış: Ayşe aynı ölçümü kendi makinesinde yeni kayıtla koşar (örn. E-012), eski kayda `- **Re-Measured-By:** E-012 (tarih)` satırı ekler → denetim `CROSS-MACHINE` uyarısı verir (hata değil). Kişisel ayarlar `*.user.toml`'da (gitignored), takım ayarları commitli TOML'da. Dashboard olarak blackboard: `write --hot`, run list, handoff zinciri (#14).

---

## 6. P7–P18 — Teslim Zinciri Playbook'ları

### P7 — Tam feature akışı (mutlu yol, uçtan uca)

**Tetikleyici:** "Şu feature'ı ekle" — zincir zaten kurulu (P1/P2/P3 geçilmiş).

```
E-045 (bench: scripts/bench/bench_bfs.py → APPROVED, VERIFIED)
 → IR-012 (READY: E-045 + PRD + mimari girdi)
  → SP-003 (hedef tek cümle, S-027 3 puan, kapasite 20'de 18, borç time-box'lı)
   → S-027 (frontmatter refs + 3 AC + task↔AC + DoD-NNN)
    → kod (src/search/** — scope İÇİ)
     → QR-028 (coverage %87, testler yeşil, review APPROVED)
      → git commit (quality: IR✓ QR✓ SP✓)
       → PR-007 (staging PASS, rollback testli, alertler kurulu)
        → deploy (deploy gate: IR✓ QR✓ SP✓ PR✓)
         → Deploy Result doldur → done
```

Komut iskeleti:

```bash
cp templates/_template_E.md docs/experiments/E-045.md        # doldur
python3 {metodoloji-root}/skills/bmad-research-experiment/scripts/run_experiment.py \
  --record docs/experiments/E-045.md --run "python scripts/bench/bench_bfs.py"
# APPROVED → IR/SP/S doldur → kodla → QR doldur → commit → PR doldur → deploy
python3 scripts/create-qr-record.py --story docs/development/stories/S-027.md  # QR iskeleti
git commit -m "feat(search): S-027 bfs ..."
```

Blackboard eşliğinde (önerilir): PRD skill'i `write --hot prd.acme`, UX'e `mirror --to bmad-ux`, mimari karar haritasını canvas'a, açık soruları run list'e. Kapanışta `doctor --json` + `hot --clear` + mirror. Her halkanın alanlarını #8'den doldur.

### P8 — Fikir → Beyin Fırtınası → Mimari → PRD → Epic → Story → Deney → Üretim (ana zincir)

**Tetikleyici:** Ham fikirden üretime kadar tüm hattı yürüteceksin (P7'nin "öncesini" de kapsar).

```
Fikir
 → forge-idea (basınç testi: yaşar / ucuz ölür)
  → brainstorming (tekniklerle dallandır: SCAMPER, altı şapka, zihin haritası)
   → PRD (bmad-prd: hedef, kapsam, NFR, AC taslağı) + UX (gerekiyorsa bmad-ux)
    → Mimari (bmad-architecture: karar omurgası, modül sınırları, Code Scope taslağı)
     → check-implementation-readiness (eksik PRD/UX/mimari varsa DUR — uydurarak geçme)
      → Epics + Story'ler (bmad-create-epics-and-stories → bmad-create-story: draft)
       → IR-00N (READY: E/R/D/C girdileri + PRD + mimari + başarı kriterleri)
        → Deney E-00N (bench scripts/bench/ altında, --dry-run → gate → --verify=VERIFIED)
         → Story finalize (experiment_refs + AC dörtlüsü + task↔AC + DoD-NNN)
          → SP-00N (tek cümle hedef + story listesi + kapasite + borç)
           → Üretim (bmad-dev-story / quick-dev: red-green-refactor, scope İÇİ kod)
            → QR (docs/quality/) → commit (quality: IR→QR→SP) → PR → deploy
```

Blackboard rölesi bu sırayla akar: `prd → ux → architecture → spec → create-epics-and-stories → create-story → dev-story`; her skill bitince `mirror --key <run-key> --value ... --to <sonraki>` atar (tek çağrı: kalp atışı + sinyal, tekrarı sinyali kopyalamaz), sonraki `handoffs --skill <self>` + `consume` ile devralır. Deney skill'i (`bmad-research-experiment`) APPROVED'da gate aynasıyla `bmad-check-implementation-readiness`'e sinyal bırakır; dev skill'i bitince `bmad-code-review`'e sinyal bırakır — bu terminal kapı `chain-health`'te birinci sınıf bir hop olarak raporlanır ve gönderen imzası `--sender bmad-dev-story` ile açıkça yazılır (`story.` namespace'ini create-story ile paylaştığı için).

**Kritik sıra notları:**

1. Deneyi story DRAFT'ından SONRA, story FINALIZE'ından ÖNCE koş: scope'u story'den alırsın, `experiment_refs`'i doğrulayıp story'yi kapatırsın.
2. IR'ı iki kez yokla: bir kez mimariden sonra (draft readiness), bir kez deneyden sonra (READY kararı). İkincisi olmadan SP açma.
3. Mimari, Code Scope taslağını üretir; deney onu daraltıp kilitler. Mimarisiz deneye scope uydurma.
4. REJECTED/ADVISORY-BLOCK yersen zincir BAŞA SARMAZ — sadece deney halkası yenilenir (yeni E kaydı), PRD/mimari/story draft'ları durur.
5. Her halkanın çıktısı bir sonraki skill'in girdisidir: PRD'siz epic, epicsiz story, storysiz deney, deneysiz kod YOK.

### P9 — E REJECTED oldu

Gate `REJECTED` yazdı → kodu açmaz. Hipotezi revize edip **yeni kayıt** aç (E-002), eski kaydı elle APPROVED'a çevirmeye kalkma (FORGED üretirsin). E-002'nin `Theory`'sine "E-001 reddedildi çünkü..." notunu düş — zincir okunabilir kalsın. Kullanıcıya "hangi ölçüm eşiği tutmadı" diye söyle (#19).

### P10 — ADVISORY-BLOCK (token gerçek, kod kapalı)

`--verify` exit 2 + `ADVISORY-BLOCK`: küçük örneklem (Wilson alt sınırı eşik altı) / `n unknown` / metrik MISMATCH. Çözüm: örneği büyüt (bench'te denominator x/y'yi büyüt), metriği kayıtla eşleştir, **yeni kayıtla** yeniden ölç. ADVISORY kaydını "biraz zorlasam guard açar mı" diye kurcalama — açmaz, tasarım bu.

### P11 — FORGED / cross-machine

**Belirtiler:** `--verify` → `FORGED`. **Nedenler:** gate-yazımlı alan (claim/measured/command/token) elle editlendi; veya kayıt başka makinede imzalandı.

- İlkiyse: kaydı yeniden üret (`--record ... --run <komut>`).
- İkincisiyse: #8'deki `Re-Measured-By` prosedürü — aynı ölçümü kendi anahtarınla yeni kayıt altında koş, eski kaydın sonuna düz satır ekle.

Asla token'ı elle yeniden yazma, gate-yazımlı alanı düzeltme. İkinci yol *gerçekten* forging olur.

### P12 — Story yazarken takılma


| Semptom                                   | Neden                                                   | Senin aksiyonun                                                                                                                    |
| ----------------------------------------- | ------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `experiment_refs` deny                    | E dosyası yok / APPROVED değil / VERIFIED değil         | `ls docs/experiments/E-*.md` + `--verify` ile bul; yoksa E aç                                                                      |
| AC metadata eksik                         | committe quality gate'e takılır (hard=deny, soft=uyarı) | Yazarken bloklanmaman erteleme ruhsatı değil: story'yi bitirirken AC'nin 4 alt alanını (`Experiment/Type/Measured/Verify`) tamamla |
| Orphan task (`AC: AC-999` ama AC-999 yok) | Task↔AC bağı koptu                                      | Her task'ın AC'si mevcut olacak şekilde düzelt                                                                                     |
| DoD maddesinde `DoD-NNN` yok              | Kimliksiz DoD denetimden geçmez                         | Kimlik + `Verify:` ekle                                                                                                            |


### P13 — Sprint taştı / story blocked

SP-003'te S-031 bitmedi: Sprint Review'e "✗ NOT COMPLETED (neden)" yaz, velocity'yi güncelle, Retrospective'e aksiyon ekle (sahip + ne değişecek). Story `blocked`'a alınca Blocker notunu doldur (tarih → sahip → durum). Blocker kalıcıysa SP'nin Blocker+Dependency bölümünü güncelleyip IR'e dönmeyi değerlendir (Gaps → araştırma modu A/B/C/D + tahmini süre).

### P14 — Kod sırasında yeni araştırma sorusu çıktı

Akışı durdurma, zincire ekle: soruyu formüle et → mod seç (ölçülebilirse A, değilse B/C/D) → yeni kayıt aç (`docs/experiments/E-0NN` veya `docs/research/`, `docs/design/`) → onay/gözden geçirme sonrası developmente dön. **B/C/D kayıtları kodu açmaz — kod gerekiyorsa Mode A şart.**

### P15 — Bugfix / hotfix

Küçük düzeltme diye zinciri atlama. Dar scope'lu hızlı E (örn. `src/payments/stripe.py` tek dosya) + bench (regresyon testi) → APPROVED → düzelt → QR (küçük de olsa: test sonucu + review) → commit → PR (hotfix release type, rollback adımı). Hotfix'te PR'ın rollback + kill-switch bölümleri hayati — boş bırakma.

### P16 — Tech-debt döngüsü

QR'da borç çıktı → `docs/development/tech-debt.md`'ye satır ekle + koda `// TODO: [TD-XXX] açıklama` + öncelik (P0/P1'e hedef sprint ZORUNLU). Sonraki SP'ye borç kalemini time-box'lı koy. Ödenince: Active→Paid tablosuna taşı (çözüm+sprint+QR-id), TODO'yu sil. `check-techdebt.sh` (#6b) drift/ID/P0/orphan denetler — periyodik koş.

### P17 — Incident → PM

SEV1/SEV2 sonrası 24-48 saat içinde `docs/development/incidents/PM-XXX.md` (blameless: suçlu değil sistem). Zaman çizelgesi (UTC), etki, 5 Neden, tespit/müdahale analizi, dersler, aksiyonlar (sahip+deadline+durum). PM'den çıkan borç TD kaydı → sonraki sprint. PR'ın "Deploy Result"una PM-id'yi işle.

### P18 — Paralel deneyler

Birden çok E aynı anda aktif olabilir — guard hedef dosyanın **herhangi** bir VERIFIED scope'a girip girmediğine bakar. Scope'ları çakıştırmadan dar tut (çakışma = hangi deneyin kanıtladığı belirsizleşir). Commit mesajına/story'e ilgili E-id'yi yaz.

---

## 7. P19–P27 — Sürdürme Playbook'ları

### P19 — Soft → hard sıkılaştırma

Brownfield başlangıcında soft olan kapıları ilk VERIFIED scope'tan sonra hard'a al:

```toml
[hooks]
code_guard = "hard"
quality_gate = "hard"
deploy_guard = "hard"
# stop_guard: etkisi yok (report-only), uyumluluk için durur
```

`/metodoloji:audit` #5b canlı modu gösterir. Config çağrı başına okunur — reload yok. Sıkılaştırdıktan sonra bir `git commit` + bir korumalı-dışı yazım denemesiyle deny'yi gör (negatif test).

### P20 — Commit / deploy disiplini

- Her commit öncesi: done story'lerin QR'ı var mı? SP referansları mevcut mu? IR var mı? (quality sırası IR→QR→SP).
- Soft modda uyarı görürsen kaydı tamamlamadan "nasıl olsa geçti" deme — hard'a geçince aynı eksik deny olur.
- Deploy komutları regex'le tanınır (#9.4). `git push origin main` deploy sayılır — PR'sız push hard modda DENY yer. Bilerek.

### P21 — Audit log izleme

```bash
cat .metodoloji/logs/hook-audit.log
tail -10 .metodoloji/logs/hook-audit.log
grep '"tool": "file_editor"' .metodoloji/logs/hook-audit.log
grep 'methodology_warnings' .metodoloji/logs/hook-audit.log
```

Gövde 300-karakter önizlemeli — tam içerik bekleme (tasarım). `session_stop` işaretleriyle oturum sınırlarını ayır. "Oturumda ne yaptık" sorusunun cevabı burada: `session_start`–`session_stop` arası kayıtlar.

### P22 — Yerel periyodik denetim

Değişiklikten sonra yerelde koş (repo kökünde CI workflow'u yok — denetim yereldir):

```bash
python -m pytest -q
sh scripts/check-custom.sh
sh scripts/check-plugin.sh
sh scripts/check-methodology.sh
sh scripts/check-techdebt.sh
python scripts/check-handoff.py
```

Sıra önerisi: önce pytest (motor davranışı), sonra check-plugin (en geniş statik yüzey), sonra diğerleri. `check-methodology.sh` kayıtsız taze kurulumda uyarıyla exit 0 verir — korkma.

**CHECK 8 (şablon hijyeni):** kayıtta `> This template is used…` banner'ı, yinelenen `## <tür>: <id>` başlığı veya `[etiketi tekrarlayan yer tutucu]` görürsen: **şablonda** → ISSUE (kaynağı düzelt), **üretilmiş kayıtta** → WARN (yazılmış verdict sessizce değiştirilmez; sonraki kayıt temiz şablondan kopyalanır).

Negatif testler (kapıların gerçekten çalıştığının kanıtı):

```bash
sh scripts/check-plugin.sh --negtest   # 7 aşama: .env/.gitignore, 2 BRIDGE sökme (#2b), #1b hooks.json, #6c template, #6d marker, #6e help catalog → hepsi yakalanıp restore edilir
sh scripts/check-custom.sh --negtest   # 3 test: #3 hard-gate + #7 bridge drift ×2 (#2.3 silme + "bolum N.N" enjeksiyonu)
```

### P23 — Blackboard ile ekip akışı

PRD → UX → mimari → spec → epics → story → dev rölesini handoff kanallarıyla taşı (#14). Her sabah `handoffs` (bekleyenler), `chain-health` (hop başına bekleyen/tüketilen), `doctor --json` (NEEDS ATTENTION). Canvas'la canlı resim (doc-map'e `watch docs/` + skill'lerin `touch` bildirimleri). Oturum başında PROACTIVE dürtme kimin batonu almadığını söyler — duyurur, tüketmez; ilgili skill `consume` eder.

### P24 — TOML ile takımı terbiye etme

- Takım kuralı (commitli): `custom/{skill}.toml` — örn. review titizliği, risk eşiği.
- Kişisel (gitignored): `custom/{skill}.user.toml` — örn. dil tercihi.
- Silme yok: kaldırmak yerine fork veya noop override. Değişiklikten sonra `resolve_customization.py -k ...` ile BRIDGE'in runtime'da göründüğünü doğrula (#2b'nin baktığı şey bu).
- Proje-seviyesi: `{project-root}/bmad/config.toml` (takım) + `.user.toml` (kişisel) — plugine dokunmadan konfigürasyon.

### P25 — Plugin güncelleme

```bash
cd /path/to/metodoloji
git pull
python -m pytest -q && sh scripts/check-plugin.sh
```

`hooks.json`'da local edit'in varsa `sync-hooks-json.py --write` ile yeniden üret (yoksa #1b byte-identical hatası alırsın — bu hata seni koruyor).

### P26 — `.env` hijyeni (#6a)

`.env` repo'da olmayacak, `.env.example` olacak, `.gitignore` `.env`'i kapsayacak. Denetim #6a üçünü de kontrol eder. Negatif testin 1. aşaması bunu kanıtlar.

### P27 — Beyan edilen workflow'u koşturma (süreç motoru)

**Tetikleyici:** Kullanıcı çok adımlı bir süreç istiyor ("SEO görünürlüğünü düzelt", "şu akışı baştan sona yürüt").

Skill'ler "bir adımı nasıl yapacağını", hook'lar "hangi dosyaya dokunulduğunu" bilir; **sırayı** bilen katman workflow çekirdeğidir: bir **niyet**, beyan edilen bir sürece dönüşür ve çekirdek onu deterministik bir durum makinesi olarak çalıştırır. Model spec'i yazar ve aşamanın işini yapar; **sıradaki aşamayı model seçmez, çekirdek hesaplar.**

**Neden ayrı bir çekirdek:** blackboard bir *bağlam ağı*dır (key/list/canvas/link/alert) — aşama, önkoşul ve geçiş kavramı yoktur. `bmad-loop` ise dev/review oturumlarını yöneten harici bir pakettir. Süreç motoru bunların semantiğini miras almadan kendi başına durur.

**Nasıl çalışır:**

- **Spec veridir.** `id`, `start`, `stages[]` — her aşamada `evidence` (kanıt) ve `next` (geçiş). Önce yapı doğrulanır, sonra çalıştırılır.
- **Kanıt, iddia değil.** Terminal olmayan her aşama kanıtını beyan eder: `artifact` (dosya + token + min sayı), `command` (argv + beklenen çıkış kodu), `note` (`--note` zorunlu). Kanıt yoksa `complete` **reddeder** — "adım atlanmadı" garantisi buradan gelir.
- **Geçişler hesaplanır.** Koşullu bir `next` kenarı bayrağı okur (`regressed=true` gibi); o kenar alındığında bayrak **tüketilir**, böylece döngü bir kez çalışır ve sonra varsayılan kenara düşer.
- **Sıra mekanik, hook'lar uyarıcı.** Sırayı çalışma zamanı kanıtla zorlar; hook'lar yalnızca konumu bildirir (SessionStart satırı aktif run'ı, aşamasını ve hesaplanan sonraki aşamayı söyler). Kod yazımı yine E kaydının gate'ine bağlıdır — çekirdek gate'in yerine geçmez.

```sh
python3 {metodoloji-root}/bmad/scripts/workflow.py validate --spec dosya.json --project-root {project-root}
python3 {metodoloji-root}/bmad/scripts/workflow.py create --spec dosya.json --project-root {project-root}
python3 {metodoloji-root}/bmad/scripts/workflow.py create --builtin seo-visibility --project-root {project-root}
python3 {metodoloji-root}/bmad/scripts/workflow.py list --project-root {project-root}
python3 {metodoloji-root}/bmad/scripts/workflow.py status --project-root {project-root}
python3 {metodoloji-root}/bmad/scripts/workflow.py next --project-root {project-root}
python3 {metodoloji-root}/bmad/scripts/workflow.py complete --slug seo-visibility --stage analyze --project-root {project-root}
python3 {metodoloji-root}/bmad/scripts/workflow.py flag --slug seo-visibility --key regressed --value true --project-root {project-root}
python3 {metodoloji-root}/bmad/scripts/workflow.py block --slug seo-visibility --reason "GSC erişimi yok" --project-root {project-root}
python3 {metodoloji-root}/bmad/scripts/workflow.py resume --slug seo-visibility --project-root {project-root}
```

Her komut JSON basar: başarıda exit `0`, reddedilişte exit `1` (`ok:false` + eksik olanı söyleyen `error` alanı). Durum `{project-root}/.metodoloji/workflow/` altında tutulur ve **elle düzenlenmez**.

**Hazır örnek — `seo-visibility` builtin spec'i:** `analyze` (sorunları `sorunlar.md`'ye `ISSUE-` ile listele) → `map` (her sorunu koda `MAP:` ile bağla) → `plan` (senaryoları puanla, `FIX:` ile seç) → `apply` (`APPLIED:`) → `test` (`RESULT:`; başarısızsa `regressed` bayrağıyla `apply`'a geri dön) → `done`.

Detaylar: `skills/bmad-workflow/authoring.md` (spec yazımı), `skills/bmad-workflow/running.md` (run sürme), süreç motoru kodu `bmad/workflow/`. 

---

## 8. Kayıt Zinciri Referansı E → IR → SP → S → QR → PR

Her kaydı **şablondan** aç (`{metodoloji-root}/templates/`), alanları doldur, kapısını koştur. Tablo, kimi nereye yazacağını söyler:


| Kayıt  | Tip    | Kapı                                    | Çıktı dizini                                                         |
| ------ | ------ | --------------------------------------- | -------------------------------------------------------------------- |
| **E**  | Mode A | Mekanik gate (`GATE-OK-...`)            | `docs/experiments/E-NNN.md`                                          |
| **IR** | Gate 1 | Araştırma kayıtları onaylı mı           | `docs/development/IR-NNN.md`                                         |
| **SP** | Gate 2 | S-id backlog + kapasite + borç kontrolü | `docs/development/SP-NNN.md`                                         |
| **S**  | Dev    | Task↔AC eşleşmesi + deney referansı     | `docs/development/stories/S-NNN.md`                                  |
| **QR** | Gate 3 | Coverage ≥ %80, test/lint temiz         | `docs/quality/QR-NNN.md` (kanonik; `docs/development/` legacy kabul) |
| **PR** | Gate 4 | Staging + rollback + monitoring         | `docs/development/PR-NNN.md`                                         |


### 8.1 E — Experiment (Mode A, mekanik gate)

**Koda giden tek meşru yol.** Deneysiz kod yazımı guard tarafından mekanik olarak kesilir.

**1. Dosyayı oluştur:**

```bash
cp templates/_template_E.md docs/experiments/E-001.md
```

**2. Zorunlu alanları doldur (`REQUIRED_DRAFT` — bunlar yoksa gate çalışmaz):**

```markdown
## Experiment: E-001 — DB index optimizasyonu
- **Date:** 20.08.2026
- **Status:** planned
- **Theory:** Büyük tablolarda B+ tree index aramayı O(n)'den O(log n)'e düşürür
- **Hypothesis:** H-001: "query_time_ms <= 20"
- **Measurement Metrics:** query_time_ms <= 20
- **Experiment Design:** girdiler, prosedür, kontrol değişkenleri, tekrarlanabilirlik
- **Sample Size n:** 40 (bilgilendirici — gate x/y'yi ölçüm çıktısından parse eder)
- **Code Scope:** src/db/**/*.py, lib/engine/*.py
```

> **İngilizce alan etiketleri ZORUNLU** — gate onları parse eder. Türkçe etikete çevirirsen gate kaydı tanımaz.

`Code Scope` glob sözdizimi: `**` her derinlik, `*` tek segment, `?` tek karakter; virgül/boşlukla ayrılır; `none` = kod üretmeyen deney.

**3. Gate'i çalıştır (ölçümü gate kendisi koşar, operatörün beyan ettiği sayıyı kabul etmez):**

```bash
python3 {metodoloji-root}/skills/bmad-research-experiment/scripts/run_experiment.py \
  --record docs/experiments/E-001.md \
  --run "python scripts/bench/bench_query.py"
```

Kurallar:

- Ölçüm scripti **hiçbir free yüzeyde olamaz** (`scratch/`, `tmp/`, `temp/`, `graft/`, `openhands/`, `_bmad/`, `.metodoloji/`, kök `explore_*` yasak) — `scripts/bench/` gibi korumalı dizine koy.
- `Decision` yazılmış kaydı tekrar koşamazsın — yeni kayıt aç.
- `--measured` parametresi kaldırıldı — gerçeklik mekaniktir.

**4. Sonuç:** `APPROVED` → guard bu scope'u açar; `REJECTED` → hipotezi revize edip yeni kayıtla yeniden ölç (P9).

**5. Kod yazmadan önce doğrula:**

```bash
python3 {metodoloji-root}/skills/bmad-research-experiment/scripts/run_experiment.py \
  --verify --record docs/experiments/E-001.md
```


| Exit | Çıktı                 | Anlam                                                                                                           |
| ---- | --------------------- | --------------------------------------------------------------------------------------------------------------- |
| `0`  | `VERIFIED`            | APPROVED + gerçek token — scope içinde kod serbest                                                              |
| `1`  | `FORGED`              | Token kaydın claim/measured/command'ı ile eşleşmiyor — geçersiz                                                 |
| `1`  | `REJECTED` / kararsız | Gate'den geçmedi (veya hiç koşmadı)                                                                             |
| `2`  | `ADVISORY-BLOCK`      | Token gerçek ama **kodu açmaz**: küçük örneklem (Wilson alt sınırı eşik altı), `n unknown` veya metrik MISMATCH |


**Kuru prova (Decision yazmadan önizleme):**

```bash
python3 {metodoloji-root}/skills/bmad-research-experiment/scripts/run_experiment.py \
  --record docs/experiments/E-001.md \
  --run "python scripts/bench/bench_query.py" --dry-run
```

> Format/kayıt kontrolü için `--run`'ı tek başına "bakayım" diye koşma — kaydı yanlışlıkla karara bağlarsın. Önizleme için hep `--dry-run`.

**Cross-machine:** Anahtar makine-yerel olduğundan başka makinede imzalanmış `APPROVED` kayıt senin makinende **tasarım gereği** `FORGED` verir — bu provenans bilgisidir, illa tahrifat değil. Gate-yazımlı alanları elle düzeltme, token'ı elle yeniden yazma (bu *gerçekten* forging olur). Bunun yerine: aynı ölçümü kendi anahtarınla **yeni kayıt** altında koş ve eski kaydın sonuna düz satır olarak `Re-Measured-By:` ekle:

```markdown
- **Re-Measured-By:** E-012 (17.09.2026)
```

`check-plugin.sh` #3 bunu `CROSS-MACHINE` uyarısı sayar (hata değil) — yeni kaydın kendi anahtarında verify olması şartıyla.

**Alan tablosu:**


| Alan                  | Zorunlu    | Açıklama                                                  |
| --------------------- | ---------- | --------------------------------------------------------- |
| `Date`                | Evet       | DD.MM.YYYY                                                |
| `Status`              | Evet       | planned / APPROVED / REJECTED                             |
| `Theory`              | Evet       | Hangi teori/çerçeve ("merak ettim" yetmez)                |
| `Hypothesis`          | Evet       | H-NNN: "metrik &gt;= eşik" formatı                        |
| `Measurement Metrics` | Evet       | Metrik adı + eşik, sayısal                                |
| `Experiment Design`   | Evet       | Girdi, prosedür, kontrol, tekrarlanabilirlik              |
| `Code Scope`          | Evet       | Glob listesi veya `none`                                  |
| `Sample Size n`       | Bilgi      | Gate denominator'ı çıktıdan parse eder                    |
| `Measurement Command` | Gate yazar | `--run` komutu; sonradan değişirse token bozulur (FORGED) |
| `Raw Results`         | Gate yazar | Ölçüm çıktısı                                             |
| `Uncertainty`         | Gate yazar | small sample / none / n unknown                           |
| `Metric`              | Gate yazar | consistent / MISMATCH                                     |
| `Decision`            | Gate yazar | APPROVED / REJECTED                                       |
| `Gate Evidence`       | Gate yazar | GATE-OK-...                                               |
| `Next Step`           | Gate yazar | Proceed to Code / Return to Theory                        |


### 8.2 IR — Implementation Readiness (Gate 1)

Yer: `docs/development/IR-NNN.md`. Statü: `READY` | `INCOMPLETE` (legacy Türkçe kabul).

Checklist: onaylı araştırma kaydı (E/R/D/C-id) var; PRD veya story tanımlı; gerekiyorsa UX + mimari hazır; başarı kriterleri net/ölçülebilir; teknik bağımlılıklar + risk değerlendirmesi yapılmış; boşluk varsa planı var.

**Boşluk varsa sprint başlamaz** — önce araştırma kanadına dönülür.

### 8.3 SP — Sprint Planning (Gate 2)

Yer: `docs/development/SP-NNN.md`. Statü: `planned` | `in-progress` | `completed` | `cancelled` (legacy `canceled`/Türkçe kabul).

Alanlar: tek cümlelik sprint hedefi; story listesi (S-id + öncelik + puan); kapasite (velocity'ye karşı); tech-debt değerlendirmesi; blocker + çözüm planı; bağımlılıklar.

Checklist: hedef tek cümle; her story'nin S kaydı var; puanlar gerçekçi; kapasite velocity'ye sığıyor; borç time-box'lı; blocker'ların çözüm planı var.

### 8.4 S — Story

Yer: `docs/development/stories/S-NNN.md`. Statü: `backlog` | `sprint` | `in-progress` | `review` | `done` | `blocked`.

**1. Frontmatter (zorunlu):**

```yaml
---
experiment_refs:
  - id: E-001
    scope: "src/db/**"
    status: APPROVED
---
```

**2. Acceptance Criteria (her AC için zorunlu alt alanlar):**

- `[AC-NNN]` kimliği
- `Experiment:` (E-NNN, veya `[HYPOTHESIS]` etiketli AC'de `—`)
- `Type:` (`agent-verifiable` | `user-evaluable` | `hybrid`)
- `Measured:` (`true` | `false`)
- `Verify:` (doğrulama yöntemi)

**3. Technical Tasks:** Her üst düzey task (`- [ ]`/`- [x]`) mevcut bir AC'ye `AC: AC-NNN` referansı vermeli.

**4. Definition of Done:** Her madde `DoD-NNN` kimliği (+ `Verify:`) içermeli.

**Guard doğrulamaları (story dosyası yazılırken):**

- `experiment_refs` → kayıtlar mevcut + verify'li; `PENDING`/`REJECTED` → mod ne olursa olsun **deny**.
- AC metadata / Task↔AC / DoD / zincir kontrolleri **commit zamanına** taşındı — story yazarken ara-edit bloklanmaz (tek istisna `experiment_refs` frontmatter kontrolüdür, yazarken deny verir).

**Native story'den kayıt üretimi:**

```bash
python3 scripts/create-methodology-record.py --story docs/development/stories/S-001.md
```

### 8.5 QR — Quality Review (Gate 3)

Yer: `docs/quality/QR-NNN.md` (kanonik; `docs/development/` legacy kabul). Statü: `in-review` | `APPROVED` | `REJECTED` | `REVISED`.

Mekanik (otomatik): coverage ≥ %80; tüm testler yeşil; linter/formatter temiz; security scan temiz; performans regresyonu yok.

Dokümantatif (manuel): kod review onayı; dokümantasyon güncel; breaking change göç planı; tech-debt kaydı (`docs/development/tech-debt.md`).

QR şablonu iki standart tablo ister (denetim bunları yapısal doğrular):

```markdown
| AC | Status | Method | Evidence |
| DoD Item | Status | Evidence | Date |
```

Oluşturma:

```bash
python3 scripts/create-qr-record.py --story docs/development/stories/S-001.md
```

### 8.6 PR — Production Readiness (Gate 4)

Yer: `docs/development/PR-NNN.md`. Statü: `preparing` | `READY` | `WAITING`.

Bölümler: staging testi (deploy, smoke, entegrasyon); rollback planı (tetikleyici, adım, DB rollback); monitoring/alerting; feature flag (kill switch, kademeli); runbook; incident response (iletişim, severity, post-mortem şablonu `docs/development/incidents/PM-XXX.md`); deploy penceresi.

Checklist: tüm mekanik PASS; rollback hazır+testli; monitoring kurulu; pencere belli; değişiklik onayı alınmış. Deploy sonrası "Deploy Result" bölümü doldurulur (metrikler, varsa PM-id).

---

## 9. Hook Motoru ve Mekanik Kapılar

Senin için pratik anlamı: **Araç çağrısı yapmadan önce hangi kapının bakacağını bil.** Deny'i teşhis etmenin yolu bu bölüm.

### 9.1 hooks.json — 4 nokta, tek motor


| Hook              | Matcher                                                    | Politika                                 | Timeout |
| ----------------- | ---------------------------------------------------------- | ---------------------------------------- | ------- |
| SessionStart      | —                                                          | fail-open (bağlam enjeksiyonu)           | 10s     |
| PreToolUse `pre`  | Write|Edit|MultiEdit|Bash|PowerShell|file\_editor|terminal | fail-closed (içinde guard)               | 10s     |
| PostToolUse audit | Write|Edit|MultiEdit|Bash|PowerShell|file\_editor|terminal | fail-open (log-only)                     | 2s      |
| Stop              | —                                                          | fail-open (report-only, asla engellemez) | 5s      |


Tek `pre` girişi **guard → quality → deploy**'u tek prosesste koşar (ilk deny kısa devre, soft uyarılar birikir). `pre`'nin ötesinde modlar: `guard`, `quality`, `deploy`, `audit`, `stop`, `session_start`.

Üç kapı kendi config anahtarını korur — paylaşılan sadece prosestir (eskiden çağrı başına 3 dispatch + 3 python cold-start vardı). Union matcher sayesinde Claude `Bash` çağrısı da guard'a ulaşır — `echo x > src/a.py` gibi shell yazımı artık deney kapısını dolanamaz. Normal çağrı \~40ms sürer; 10s tavan askıda kalma güvenlik ağıdır.

Hook komutları plugin kökünü kendisi bulur (`$CLAUDE_PLUGIN_ROOT`, `$METODOLOJI_PLUGIN_ROOT`, marketplace cache, OpenHands kurulum dizini; ilk `hooks/scripts/run-hook.sh` bulunan kazanır). `hooks.json` **üretilir** — kanonik dispatch listesi `scripts/sync-hooks-json.py` + `hooks/scripts/run-hook.sh` içindeki discovery listesidir. Yol değiştirirsen `python3 scripts/sync-hooks-json.py --write` koş; `check-plugin.sh` #1b byte-identical doğrular.

### 9.2 Guard (PreToolUse) — Fail-Closed, önce koşar

Kapsam: `Write`/`Edit`/`MultiEdit` + `Bash`/`PowerShell` + `file_editor`/`terminal`/`notebook_editor` (dahilde `file_editor`/`terminal`'e normalize edilir). `PowerShell`, Claude Code'un Windows kabuk aracıdır; matcher'da olmazsa `Set-Content src/a.py ...` yazıları guard'ı hiç görmez.

Davranış:

- Story dosyası (`S-NNN.md` veya `N-N-slug.md`) yazımında: `experiment_refs` doğrulanır (`PENDING`/`REJECTED` → hep deny); yeni dosyada yinelenen kayıt ID taranır (mevcut dosya editinde değil — duplicate sadece yaratılışta doğar).
- Kod hedefi + free zone dışı → **kapsayan VERIFIED** deney aranır. Yoksa `DENY` (hard) veya `allow + methodology_warnings` (`code_guard="soft"`).
- Terminal komutundaki `$var`/`${var}` hedefleri tek tek düşürülür — `$var` asla literal path sayılmaz, ama aynı komuttaki statik hedefler hâlâ denetlenir.
- Story dosyasına terminal/heredoc yazımı: payload görünürse `experiment_refs` doğrulanır; opaksa "commit zamanına ertelendi" uyarısı verilir.
- Secret referansı (terminal komutu veya yazılan içerik) → `DENY`. İçerik taraması free-zone kontrolünden **önce** koşar — agent zone'ları bypass edemez.

**Kod hedefi sınıflandırması (whitelist — bilinmeyen uzantı korumalıdır):**


| Kategori          | Örnek                                                                          | Korumalı? |
| ----------------- | ------------------------------------------------------------------------------ | --------- |
| Kod basename      | `Makefile`, `Dockerfile`, `CMakeLists.txt`, `Justfile`, `Taskfile.*`           | ✅         |
| Kod dizini        | `src/`, `lib/`, `tools/`, `bin/`, `core/`, `app/`                              | ✅         |
| Exec config       | `.github/workflows/*`, `.gitlab-ci.yml`, `docker-compose*.yml`, `package.json` | ✅         |
| Bilinmeyen uzantı | non-code listesinde olmayan her şey                                            | ✅         |
| Toolchain config  | `prisma/vite/jest.config.*`, `tsconfig*.json`, lockfile'lar                    | ❌ serbest |
| Doküman           | `.md`, `.txt`, `.rst`                                                          | ❌ serbest |
| Veri/config       | `.json`, `.toml`, `.yaml`, `.csv`, `.log`, `.lock`                             | ❌ serbest |
| Medya/asset       | `.png`, `.jpg`, `.svg`, font, arşiv                                            | ❌ serbest |
| Meta              | `.gitignore`, `README`, `LICENSE`, `.editorconfig`, `.npmrc`                   | ❌ serbest |


### 9.3 Quality (PreToolUse) — Config-gated (varsayılan soft), ikinci koşar

Kapsam: sadece `git commit` içeren `Bash`/`terminal`.

Zincir: done story var ama hiç IR yok → deny (Gate 1) → QR'sız done story → deny (Gate 3) → SP kaydı eksik referans → deny (Gate 2). Sıra: IR (proje) → QR (story) → SP (story).

**QR-coherence adımı (SP-022'den beri quality'nin 4. adımı):** done story + APPROVED QR'a rağmen story'nin Definition of Done tablosunda `pending`/`⏳` satırı kalmışsa commit `DENY` yer (hard) veya uyarı alır (soft) — quality tablodaki statü metnini okur, token doğrulamaz. Gerekçe, çözümü de söyler: `python3 scripts/sync-story-qr.py --apply` ile QR'daki geçmiş sonucu story'ye geri senkronla, sonra coherence'i yeniden doğrula (done S + APPROVED QR ⇒ sıfır pending satırı — `bench_sp021.py` ağaç durumunu, `bench_sp020.py` operasyonu pinler). Muhafazakâr atlamalar: QR kaydı henüz doğmamış in-progress story'ler ve desteklenmeyen tablo düzenleri denetlenmez (deny değil, sessiz geç).

`quality_gate="soft"` ise deny → `allow + methodology_warnings`; `"hard"` ise bloklar. Config çağrı başına canlı okunur — reload gerekmez.

### 9.4 Deploy (PreToolUse) — Config-gated (varsayılan soft), son koşar

Deploy komutu yoksa `allow`. Varsa aynı zincir + **PR**: IR → QR → SP → PR. `deploy_guard` soft/hard mantığı quality ile aynı.

Tanınan deploy komutları (regex, case-insensitive): `terraform apply|destroy|plan`, `kubectl apply|rollout|deploy`, `docker (compose) up|deploy`, `ansible playbook|deploy`, `git push origin|upstream main|master|production|prod`, `deploy`.

### 9.5 Audit (PostToolUse) — Fail-open, senkron

- Her çağrıyı tek JSON satırı olarak ekler. Gövde 300-karakter önizlemeye redakte edilir (`content`, `code`, `source`...); path/komut/flag bütün kalır (stop çalışsın diye); tam gövde loga girmez.
- QR DoD uyarısı warn-only'dir (done-story→QR dizin taraması per-write hot path'te değil `check-plugin.sh`'tedir). QR DoD, story DoD ile **aynı parser/kuralla** doğrulanır (`.utils.dod_issues`): her DoD maddesinde kimlik + doğrulama kaydı gerekir (story'de `Verify:`; QR'da `Verify:`/`Evidence:` satırı veya `- DoD-NNN …` bullet'ında sonuç işareti `→ ✓ PASS`, veya `| DoD Item | Status | Evidence | Date |` tablosunda dolu hücre).
- Log-only: blackboard okuma/yazma yok, canvas/bridge mutasyonu yok — motor hot path blackboard-free'dir.
- Log yazım hatası fail-open (stderr notu, asla crash).
- **Senkron** koşar (`"async": true` yok): 2s timeout'lu fail-open modda fonksiyonel kayıp olmaz; ayrıca async PostToolUse yarışından doğan `InputValidationError: Bash was called with input that could not be parsed as JSON` semptomunu azaltır. Gerçek kök neden genelde pluginin Claude'da **iki kez** kayıtlı olmasıdır ("2 async PostToolUse hooks completed" görürsen duplicate kurulumu temizle: manifest + manuel `settings.json` girişi + stale marketplace cache).

### 9.6 Stop — Report-only, fail-open (asla engellemez)

1. Audit log'a `session_stop` işareti basar.
2. Hep **`allow`** döner + tek satırlık warn-only rapor: `sprint-status.yaml`'daki in-progress story'ler (oturum başlangıcından eski dosya stale brownfield artığı sayılıp yoksayılır) + **bu oturumun** kod yazımları (`session_start` işaretinden sonraki PostToolUse kayıtları; eski dosyalar görünmez; `$var` hedefleri düşürülür) + bekleyen sinyal varsa PROACTIVE handoff dürtmesi (salt-okunur peek — tüketmez, handshake'i ilgili skill tamamlar).
3. Sprint status arama sırası: `docs/development/native/sprint-status.yaml` → legacy `bmad-output/implementation-artifacts/...` → legacy `_bmad-output/...` → `.metodoloji/sprint-status.yaml`.
4. `stop_guard` config anahtarı geriye uyumluluk için durur ama **blok etkisi yoktur**. Eskiden "Stop oturumu kapatmıyor" sanılan durumun iki gerçek nedeni vardı: duplicate Stop kaydı ("Ran 2 stop hooks" → `/hooks` ile birini kaldır) veya guard/quality deny'lerini stop'a yormak. Zorlama yazma anında (guard) ve commit anındadır (quality/deploy).

### 9.7 hook-entry.sh — Tek dispatch

```
hook-entry.sh pre      → birleşik PreToolUse: guard → quality → deploy (fail-closed)
hook-entry.sh guard    → sadece guard (fail-closed)
hook-entry.sh quality  → sadece quality (soft/hard)
hook-entry.sh deploy   → sadece deploy (soft/hard)
hook-entry.sh audit    → audit (fail-open, senkron)
hook-entry.sh stop     → stop (fail-open, report-only)
```

`hooks.json` sadece birleşik `pre`'yi dispatch eder; tekil modlar direkt çağrı + `check-plugin.sh` #1 smoke testi için geçerlidir.

- Runtime: 2. CLI arg &gt; `METODOLOJI_RUNTIME` env &gt; varsayılan `openhands`.
- Python çözümleyici: `python3 → python → py`, sonra yaygın Windows yolları.
- Motor/Python yoksa: pre/guard → `DENY` + exit 2 (fail-closed); quality/deploy/audit/stop/session\_start → sessiz geç (fail-open).

### 9.8 Bash hedef tespiti


| Komut/Desen                 | Tespit edilen hedef            |
| --------------------------- | ------------------------------ |
| `> dosya` / `>> dosya`      | Yönlendirme hedefi             |
| `tee dosya`                 | Tee çıktısı                    |
| `sed -i '...' dosya`        | Sed hedefi                     |
| `cp src dst` / `mv src dst` | Son argüman                    |
| `curl -o dosya`             | -o hedefi                      |
| `tar -xf arşiv`             | Arşiv içeriği (bomba korumalı) |
| `unzip arşiv`               | Arşiv içeriği                  |
| `git apply yama`            | Yama hedefleri                 |
| `python -c 'open("x","w")'` | open() hedefi                  |


Arşiv bomba koruması (tar+zip): tek dosya ≤512MB, sıkışmış arşiv ≤64MB, üye ≤200.000, sıkışmamış toplam ≤2GB.

### 9.9 Girdi normalizasyonu

`normalize_hook_input()`: Claude `Write`/`Edit`/`MultiEdit` → `file_editor` (`file_path` → `path`), `Bash`/`PowerShell` → `terminal` (`cmd` → `command`); OpenHands aynen geçer. Runtime `METODOLOJI_RUNTIME` (`--runtime=` flag) veya ham araç adından anlaşılır. **Bilinen sapma:** tanınmayan araç adı `unknown`'a normalize olur ve guard **uyarır**, deny vermez (bilmediği bir araç çağrıyı hard-bloklamasın diye).

---

## 10. Free Zone / Korumalı Alan / Secret Taraması

Bir yazımdan önce guard'ın sorduğu sıra: **Secret tara → kirliyse DENY → free zone mu? → non-code mu? → VERIFIED deney var mı?**

### 10.1 Free zone (onaysız)


| Prefix/Dizin      | Açıklama                                                                                    |
| ----------------- | ------------------------------------------------------------------------------------------- |
| `_bmad/`          | Legacy modül verisi                                                                         |
| `scratch/`        | Prototip, geçici script, araştırma notu (gatesiz)                                           |
| `graft/`          | Graft kodu                                                                                  |
| `.git/`           | Git dizinleri                                                                               |
| `tmp/`, `temp/`   | Geçiciler                                                                                   |
| `openhands/`      | OpenHands dizinleri                                                                         |
| `.metodoloji/`    | Plugin durum dizini                                                                         |
| `docs/*.md`       | `docs/` altındaki dokümanlar                                                                |
| `docs/*/raw/`     | Ham veriler                                                                                 |
| `explore_*`       | Kök-seviye keşif DOSYASI (`explore_x.py` serbest; `explore_dir/a.py` korumalı)              |
| Altyapı dosyaları | `scripts/check-methodology.sh`, `skills/bmad-research-experiment/scripts/run_experiment.py` |


> **İki daraltma (kanıtlı):** `explore_*` yalnızca kökteki **dosyayı** serbest bırakır — `explore_foo/` dizininin içi korumalıdır (aksi halde dizin adıyla gate bypass edilirdi). Tersine, gate **hiçbir** guard-free yüzeyden ölçüm koşmaz: `scratch/`, `tmp/`, `temp/` dışındaki `graft/`, `openhands/`, `_bmad/`, `.metodoloji/`, kök `explore_*` bench'leri de reddedilir — ölçüm hep korumalı dizinde (`scripts/bench/`).

**Koşullu self-modifikasyon:** `hooks/`, `scripts/`, `skills/`, `custom/`, `bmad/tests/` (plugin kaynak ağaçları) **sadece korunan proje kökü pluginin kendi reposuysa** serbesttir (plugin kendisi üzerinde çalışırken). Sıradan hedef projede bu ağaçlar **korumalıdır** — plugin kaynağına edit de onaylı deney ister. `bmad/tests/` muafiyeti döngüsellik içindir: gate'in kendi testleri gate iznine bağlanamaz.

### 10.2 Korumalı alan (onaylı)

Tüm kaynak kod + yürütülebilir config (#9.2 tablosu). Not: gate **hiçbir free yüzeyden ölçüm scripti koşmayı reddeder** (`scratch/`, `tmp/`, `temp/`, `graft/`, `openhands/`, `_bmad/`, `.metodoloji/`, kök `explore_*`) — bench'ler `scripts/bench/` gibi korumalı dizinde durur.

### 10.3 Secret taraması (free zone'un ÖTESİNDE)

`scratch/` içinde bile şu içerikler **DENY**: `.bmad` dizini, `gate-key`, `bmad_gate_key`, `gate_token` kalıpları. `load_secret` / `secret_file` / `secret_env` sadece erişim bağlamında deny verir (çağrı/atama/köşeli — `load_secret(`, `secret_file =`, `secret_env[`) — düz yazıdaki bahsi serbesttir. Terminal komutunda `gate-key` / `.bmad` referansı → DENY.

---

## 11. Güvenlik — Gate Key, Güven Halkası ve HMAC

- Konum: `~/.bmad/gate-key` (repo DIŞI). İzin 0600 (Windows'ta yoksayılır). İçerik 64-hex (32 rastgele bayt). Ömür makine-yerel; her geliştirici kendi anahtarını üretir.
- **Güven halkası (çok-makine geliştirme):** imza tek anahtarla (bu makinenin); doğrulama halkaya bakar — kendi anahtarın + `--import-key` ile `~/.bmad/gate-keys/*.key` altına aktarılan eş makine anahtarları (malzeme `BMAD_PEER_GATE_KEY` env değişkeniyle, argv'ya asla — komut geçmişi sızdırır). Halkadaki herhangi bir anahtar altında yeniden türetilen token gerçektir; halka, token'ın açabildiği kapıyı genişletmez (claim/measured/komut/Code Scope bağları eşleşen anahtar altında yeniden denetlenir).
- Her onay HMAC-SHA256 ile imzalanır: `GATE-OK-<hash>`. Token kaydın **claim + measured + deney id + (yeni tip) `Measurement Command`** alanına bağlıdır — sonradan herhangi birini editlersen token bozulur (FORGED). `Measurement Command`'sız legacy kayıt legacy token ile doğrulanır; alanı ekleyip komutu değiştirmek downgrade sayılıp FORGED verir.
- FORGED tespiti → kayıt geçersiz, kod yazılamaz. İki çare: onaylayan makinanın anahtarını halkaya aktar (`--import-key`) — kayıt yerinde doğrulanır — ya da kaydı yeniden üret (`--record ... --run <komut>`).

---

## 12. Skill Kataloğu — Hangi Skill'i Ne Zaman Çağırırsın

Aileler:

- **bmad-**\* (çekirdek): PRD, mimari, story, sprint, dev-story/quick-dev/dev-auto, code-review, testarch-*, QA e2e, TEA, retrospective, correct-course, loop-*, eval-runner, forge-idea, brainstorming, araştırma skill'leri.
- **gds-**\* (oyun): game-brief, GDD, narrative, game-architecture, playtest-plan, performance-test, e2e-scaffold + dev/test rápido karşılıkları.
- **wds-**\* (web tasarım): 0-alignment-signoff → 8-product-evolution + freya-ux / mimir-builder / saga-analyst ajanları.
- **cis-**\* (yaratıcılık): design-thinking, innovation-strategy, problem-solving, storytelling + koç ajanları.
- **Araç/meta (bridge YOK — tasarım gereği):** `bmad-customize`, `bmad-help`, `memory`, `sync`.

**BRIDGE dağılımı (33 aktif):**

- **Producer (17, kayıt yaratır/günceller):** `bmad-dev-story`, `bmad-quick-dev`, `bmad-dev-auto`, `bmad-agent-dev`, `bmad-code-review`, `bmad-create-story`, `bmad-sprint-planning`, `bmad-check-implementation-readiness`, `gds-dev-story`, `gds-quick-dev`, `gds-code-review`, `gds-create-story`, `gds-sprint-planning`, `gds-check-implementation-readiness`, `gds-agent-game-dev`, `gds-agent-game-solo-dev`, `wds-5-agentic-development`.
- **Feeder (16, mevcut QR'ı besler):** `bmad-testarch-*` (atdd, automate, ci, framework, nfr, test-design, test-review, trace), `bmad-qa-generate-e2e-tests`, `gds-test-*` (automate, design, framework, review), `gds-e2e-scaffold`, `gds-performance-test`, `gds-playtest-plan`.
- 3'ü agent-principles yüzeyi (`bmad-agent-dev`, `gds-agent-game-dev`, `gds-agent-game-solo-dev` — BRIDGE `[agent].principles` içinde).
- Producer BRIDGE'lerde **VERIFY** adımı vardır — LLM kaydı yarattıktan hemen sonra `ls -la` ile varlığını doğrulamak zorundadır (atlanmaya karşı otomatik kontrol; denetim #2c).

**İşe göre skill seç:**


| İş                | Skill                                                                                          |
| ----------------- | ---------------------------------------------------------------------------------------------- |
| Fikirden hipoteze | `bmad-forge-idea`, `bmad-brainstorming`, `bmad-prfaq`, `bmad-product-brief`                    |
| PRD yaz           | `bmad-prd`, `bmad-create-prd`, `bmad-validate-prd`, `bmad-agent-pm`                            |
| Mimari            | `bmad-architecture`, `bmad-create-architecture`, `bmad-agent-architect`                        |
| Sprint/story      | `bmad-sprint-planning`, `bmad-create-epics-and-stories`, `bmad-create-story`, `bmad-dev-story` |
| Hızlı kod         | `bmad-quick-dev` (tek story), `bmad-dev-auto` (toplu), `bmad-agent-dev`                        |
| Kalite            | `bmad-code-review`, `bmad-quality-record`, `bmad-testarch-*`, `bmad-qa-generate-e2e-tests`     |
| Deploy hazırlık   | `bmad-production-readiness`                                                                    |
| Düzeltme rotası   | `bmad-correct-course`, `bmad-retrospective`, `bmad-teach-me-testing`                           |
| Oyun projesi      | `gds-*` ailesi (brief→GDD→mimari→story→dev→test→playtest)                                      |
| Web/UX projesi    | `wds-*` + `bmad-ux`                                                                            |
| Değerlendirme     | `bmad-eval-runner` (skill'i temiz odada koşar: transkript, süre, token, kalıcı run klasörü)    |


---

## 13. TOML Customization — 3 Katman + 8 Katman Config

### 13.1 Skill başına 3 katman (öncelik yüksekten düşüğe)

```
1. {custom}/{name}.user.toml   (kişisel, gitignored — en yüksek)
2. {custom}/{name}.toml        (takım/org, git'e commitlenir)
3. {skill-root}/customize.toml (skill varsayılanı — taban)
→ resolve_customization.py → efektif runtime config
```

`{custom}` önce proje kökündeki `custom/`'u tercih eder, yoksa Claude düzeni `_bmad/custom/`'a düşer. Plugin kuruluysa takım katmanı pluginin kendi `custom/{name}.toml`'udur (user + team aynı dizinde; merge sırası defaults → team → user).

Merge kuralları: skalerde kazanan override; tablolarda deep merge (özyineli); dizilerde elemanların hepsi aynı `code`/`id` taşıyorsa key'e göre merge, yoksa append. **Silme mekanizması YOK** — öğeyi kaldırmak için skill'i forkla veya aynı `code`/`id` ile noop açıklamayla override et.

Çözümleme:

```bash
python3 hooks/engine/resolve_customization.py -s skills/bmad-dev-story
python3 hooks/engine/resolve_customization.py -s skills/bmad-dev-story -k workflow.activation_steps_append
python3 hooks/engine/resolve_customization.py -s skills/bmad-dev-story -k agent.name -k workflow.activation_steps_append
```

(Kanonik implementasyon `bmad/scripts/resolve_customization.py` — stdlib only, py3.11+; `uv run` da olur.)

### 13.2 Merkezi config — 8 katman (son kazananlar önce)


| Öncelik       | Katman                                      | Sahibi                             |
| ------------- | ------------------------------------------- | ---------------------------------- |
| 1 (en düşük)  | `{metodoloji-root}/bmad/config.toml`        | installer (kurulumda üretilir)     |
| 2             | `{metodoloji-root}/bmad/config.user.toml`   | installer                          |
| 3             | `{metodoloji-root}/custom/config.toml`      | plugin takımı (commitli)           |
| 4             | `{metodoloji-root}/custom/config.user.toml` | plugin kullanıcısı (gitignored)    |
| 5             | `{project-root}/bmad/config.toml`           | **proje takımı (commitli)**        |
| 6             | `{project-root}/bmad/config.user.toml`      | **proje kullanıcısı (gitignored)** |
| 7             | `{project-root}/docs/config.toml`           | proje takımı, output-local         |
| 8 (en yüksek) | `{project-root}/docs/config.user.toml`      | proje kullanıcısı, output-local    |


> Legacy `{project-root}/bmad-output/config.toml` (+ `.user.toml`) migrasyon öncesi projeler için fallback olarak okunur — yazılmaz.

Proje kökü katmanları plugine dokunmadan metodolojiyi kendi repondan konfigüre etmeni sağlar. Takım ayarları commitli `{project-root}/bmad/config.toml`'a, kişisel ayarlar gitignored `.user.toml`'a:

```toml
# {project-root}/bmad/config.user.toml (kişisel — commitlenmez)
[core]
user_name = "Ayla"
communication_language = "Turkish"

# {project-root}/bmad/config.toml (takım — commitlenir)
[modules.tea]
risk_threshold = "p0"
```

Legacy YAML köprüsü: eski kurulumlardaki modül `config.yaml`'ları plugin TOML ile proje TOML **arasında** bir katman olarak okunur (plugin default'u ezer, proje TOML'una yenilir). Değerleri proje TOML'una taşıyıp YAML'ları silerek migrate et.

Flat modül çıktısı (yalnız CLI/legacy şekli):

```bash
python3 bmad/scripts/resolve_config.py --project-root . --module tea
```

Paylaşılan `core` + o modülün anahtarlarını döndürür (eski modül config.yaml şekli). `--module core` sadece core döndürür. Plugin katmanlarındaki artefakt path'leri çıktıyı `{project-root}`'a (`docs/`) işaret eder, plugin içine değil.

**Aktivasyonların kuralı bu değil:** adı geçen anahtarları ister — `--key` (tekrarlanabilir). `--module tea` 23 satır / 818 B iken hedefli okuma 5 satır / 142 B'dir; modül görünümü çalıştırmanın hiç kullanmadığı anahtarları da taşır ve "dump'ı koştur, geleni oku" alışkanlığını öğretir (bkz. #18 madde 50).

### 13.3 Manifesto wiring

Her yüzey şu dokümanlara referans vermeli: `research-methodology.md` (tüm yüzeyler), `project-context.md` (tüm yüzeyler), `development-methodology.md` (development kanadı). Köprü dokümanı: `{metodoloji-root}/docs/bmad/dev-skill-to-methodology-bridge.md` (#-numaralı — `check-custom.sh` #7 `custom/` referanslarının onunla senkron kaldığını denetler).

---

## 14. Blackboard — Çalışma Bağlamı

Tek event-sourced JSON: `.metodoloji/blackboard.json` + append-only event log. Hook'lar bağlam için okur, skill'ler odağı yazmak için kullanır. Tam tasarım: `hooks/engine/modules/blackboard.py` + `bmad/scripts/blackboard.py --help` (eski `docs/BLACKBOARD.md` prune edildi — kod kanoniktir).

**Kavramlar:** `hot`/`hot_canvas` (odaktaki tek anahtar + tek canvas — oturum başı/sonu yüzeye çıkar); `keys` (ad-alanlı metin, max 128, eskiler solar); listeler (tek anahtar altında sıralı, max 100); canvas'lar (grid/free canlı yüzey; `watch` ile dosya path'lerini izleyip `touch` bildirimini auto-cell'e çevirir); `links` (yönlü graf kenarı); subscription (glob → kanal); alert (sınırlı, kanal-adresli bildirim — `session` kanalı oturum başında, `stop` kanalı sonda enjekte edilir); `tags`/`contributions`/`watchers`.

**Değişmezler:** exclusive lock altında atomik yazım; event-sourced (snapshot silinebilir, logdan rebuild); sınırlı (hiçbir şey sınırsız büyümez); fail-open (bozuk/kayıp board işi bloklamaz); tek `hot` + tek `hot_canvas`.

**Motor yazım yolu blackboard-free'dir:** hook'lar board'a yazmaz. Salt-okunur peek'ler (fail-open, tüketmez): SessionStart'taki PROACTIVE handoff dürtmesi + zincir ilerlemesi (`Chain progress`), Stop'taki PROACTIVE dürtme. Audit log-only'dir. Board yazımı skill-tarafı CLI (`blackboard.py`) + gate aynası (`run_experiment.py --run` karar sonrası `E-<id>` + readiness hand-off'u otomatik işler) işidir.

**CLI (ezber seti):**

```bash
python3 bmad/scripts/blackboard.py write --key prd.acme --value "PRD v1: discovery" --type state --hot
python3 bmad/scripts/blackboard.py write --key prd.acme --value "PRD v1: finalize" --type state
python3 bmad/scripts/blackboard.py list-add --key prd.acme.pending --item "NFR-2'yi onayla"
python3 bmad/scripts/blackboard.py list-remove --key prd.acme.pending --item "NFR-2'yi onayla"
python3 bmad/scripts/blackboard.py list-clear --key prd.acme.pending
python3 bmad/scripts/blackboard.py canvas create --name doc-map --grid 8x8 --focus
python3 bmad/scripts/blackboard.py canvas set --name doc-map --cell A1 --content "nav hub" --kind decision
python3 bmad/scripts/blackboard.py canvas watch --name doc-map --path docs/
python3 bmad/scripts/blackboard.py canvas touch --path docs/design/spec.md --tool bmad-architecture
python3 bmad/scripts/blackboard.py canvas-read --name doc-map
python3 bmad/scripts/blackboard.py canvas focus --clear
python3 bmad/scripts/blackboard.py link --a prd.acme --b arch.acme --relation informs
python3 bmad/scripts/blackboard.py neighbors --node prd.acme
python3 bmad/scripts/blackboard.py subscribe --watcher session --pattern "prd.*" --channel session
python3 bmad/scripts/blackboard.py alerts
python3 bmad/scripts/blackboard.py consume --channel stop
python3 bmad/scripts/blackboard.py notify --channel stop --kind risk --text "NFR onaysız"
python3 bmad/scripts/blackboard.py mirror --key prd.acme --value "PRD final" --to bmad-ux --note "PRD final — NFR-3'ten başla"
python3 bmad/scripts/blackboard.py handoffs
python3 bmad/scripts/blackboard.py handoffs --skill bmad-ux
python3 bmad/scripts/blackboard.py consume --channel handoff.bmad-ux
python3 bmad/scripts/blackboard.py chain-health
python3 bmad/scripts/blackboard.py doctor
python3 bmad/scripts/blackboard.py tag --tag crm
python3 bmad/scripts/blackboard.py contribute --who bmad-prd --what "PRD v1 taslağı"
python3 bmad/scripts/blackboard.py stats
python3 bmad/scripts/blackboard.py read --context
python3 bmad/scripts/blackboard.py hot --clear
```

**Handoff handshake (kritik):** Upstream işi bitince `handoff --to <skill>` ile sinyal bırakır; sinyal downstream `handoffs --skill <self>` ile bakıp `consume --channel handoff.<skill>` ile tüketene kadar bekler — tüketim **handshake'in ta kendisidir**. Beklerken iki announce-only yüzeyde görünür (asla tüketmez): session\_start bağlam dürtmesi (delivery zinciri + methodology\_chain + alt zincirler + `bmad-help`; tool-workflow kanalları hariç) ve stop wrap-up raporu (methodology\_chain sinyalleri). Teslim rölesi: prd → ux → architecture → spec → create-epics-and-stories → create-story → dev-story.

**Terminal hop'lar (`CHAIN_TERMINAL`):** Röleyi tüketip hiçbir yere devretmeyen alıcılar da birer hop'tur — sinyal orada bekler ve orada tüketilir. Bildirilmezlerse terminal baton zincir dışı `extra` gürültüsü olarak raporlanır ve sinyali atan aşama yanlış kredilenir. Bu yüzden `bmad-dev-story → bmad-code-review` açıkça bildirilir (`chain-health` satırı `terminal: true` taşır; `chain` = 6 aşama hop'u + bu kapı). GDS bunu kendi `gds` alt zincirinde zaten böyle yapıyor; `pending_handoff_channels` içindeki elle yazılmış alıcı sabiti kaldırıldı.

**Gönderen imzası (`--sender`):** `story.` çalışma anahtarı namespace'ini **iki** aşama yazar (create-story açar, dev-story sürdürür), dolayısıyla önekten atıf yapmak dev kapanışını create-story'ye yazar. Böyle paylaşılan bir namespace'te kapanış `mirror … --sender bmad-dev-story` ile imzalanır (opsiyonel alan olay kaydına yazılır; geçersiz değer yok sayılır, öneke düşer).

**Alt zincirler (faz bazlı, `SUB_CHAINS` kayıt defteri):** İki kanonik röle dışındaki fazlar tek kayıt defterinde tanımlanır ve `chain-health` çıktısında `sub_chains` altında aynı birinci sınıf muameleyi görür — `extra` yalnızca gerçekten bilinmeyen alıcılar için ayrılmıştır. Yeni faz eklemek = kayıt defterine bir satır; `doctor` (bekleyen hop'lar), session\_start/stop dürtmesi ve statik lint kendiliğinden takip eder.

- **discovery** — fikir, araştırma ve CIS kolaylaştırıcıları brief/PRD girdisine fan-in yapar (`bmad-forge-idea`, `bmad-brainstorming`, `bmad-prfaq`, üç araştırma skill'i, dört CIS aracı (`bmad-cis-design-thinking`, `-innovation-strategy`, `-problem-solving`, `-storytelling`) → `bmad-product-brief`/`bmad-prd`; run-key önekleri `forge.`, `brainstorm.`, `brief.`, `prfaq.`, `research.market.`, `research.domain.`, `research.tech.`, `cis.design.`, `cis.innovation.`, `cis.solving.`, `cis.story.`). Yalnızca keşif amaçlı kalan bir CIS oturumu hop'unu sessiz bırakır — sinyal, üzerine inşa edilmeye değer bir yön çıktığında gönderilir. Keşif skill'leri aktivasyon tarafında **giriş noktasıdır**: hiçbir hop onlara yönelmez, dolayısıyla asla karşılanamayacak bir peek vaat etmezler.
- **alt\_dev** — geliştirme aşamasının alternatif yolları: `bmad-quick-dev` (`quickdev.`), `bmad-dev-auto` (`devauto.`) ve dev personası `bmad-agent-dev` (`agentdev.`; BRIDGE'i S→QR→PR taşır) teslim rölesinin terminal kapısı `bmad-code-review`'e yakınsar. Bunlar zincirin **giriş noktalarıdır** (peek yok, kapanışta post var) ve E→IR→SP→S→QR→PR pozisyonu damgalamaz — dal, aşama değil.
- **testing** — TEA araç kutusu kalite kaydının mekanik kontrollerini besler (`bmad-testarch-atdd`, `-automate`, `-ci`, `-framework`, `-nfr`, `-test-design`, `-test-review`, `-trace` ve `bmad-qa-generate-e2e-tests` → `bmad-quality-record`; run-key önekleri `test.<araç>.`); TEA Academy (`bmad-teach-me-testing`, önek `teach.`) mezunu öğrettiği zincire yönlendirir (`bmad-testarch-framework`). Besleyici **mevcut QR kaydına kanıt ekler**, kendi kaydını açmaz ve metodoloji pozisyonu damgalamaz. Peek yapan tek TEA workflow'u `bmad-testarch-framework`'tür — academy ona devreder.
- **governance** — döngüyü kapatan hop'lar: retrospective (`bmad-retrospective`, önek `retro.`) sınanabilir derslerini başlangıca geri verir (`bmad-research-experiment`), bir rota düzeltmesi (`bmad-correct-course`, önek `change.`) ise backlog'un sahibi olan yüzeye iner (`bmad-sprint-planning`) — yani büyük bir yeniden planlama bile zincire oradan girer. İkisi de zincirin **giriş noktalarıdır** (peek yok, kapanışta post var) ve mevcut bir röle skill'ine yeniden girdikleri için röleyi uzatmaz, pozisyon damgalamaz. Hedefleri **peek yapar**: `bmad-research-experiment` gelen dersi tüketir (dönüş kenarı bildirim değil, batondur) — artık `origin` muafiyeti yoktur, çünkü alan bir aşama tüketebilmelidir.
- **gds** — oyun dikeyi kanonik röle şeklini oyun-doğal artefaktlarla yeniden kullanır: fikir/araştırma brief'e fan-in yapar (`gds-domain-research`, `gds-brainstorm-game` → `gds-create-game-brief`), GDD birincil tasarım kapısıdır ve resmi oyun PRD'si isteğe bağlı alternatiftir (`gds-create-narrative`, `gds-prd` → `gds-gdd` / `gds-game-architecture`), ardından hazırlık → planlama → story → dev → inceleme (`gds-check-implementation-readiness`, `gds-sprint-planning`, `gds-create-story`, `gds-dev-story` → `gds-code-review`, bmad ikizini aynalayan terminal kapı). QA araç kutusu inceleme oturumunun QR kaydını besler (`gds-test-design`, `-framework`, `-automate`, `-review`, `gds-e2e-scaffold`, `gds-performance-test`, `gds-playtest-plan`), `gds-retrospective`/`gds-correct-course` planlamaya geri girer, `gds-sprint-status` canlı durumu raporlar, `gds-investigate` rota düzeltmesini besler ve dev varyantları (`gds-quick-dev`, `gds-agent-game-dev`, `gds-agent-game-solo-dev`) aynı inceleme kapısına yakınsar. Run-key önekleri `gds.<aşama>.`.
- **wds** — Freya'nın tasarım hattı numaralı fazları sırayla koşar: `wds-0-project-setup` → `wds-0-alignment-signoff` hizalama onayını tohumlar, brief `wds-1-project-brief` → `wds-2-trigger-mapping` → `wds-3-scenarios` → `wds-4-ux-design` üzerinden akar, ardından inşa fazları gelir: `wds-5-agentic-development` → `wds-6-asset-generation` → `wds-7-design-system` (terminal). Brownfield evrimi brief'ten yeniden girer (`wds-8-product-evolution` → `wds-1-project-brief`) — WDS döngüsü. Run-key önekleri `wds.<faz>.`. Kapanış sinyali: GDS aşamaları hop'unu `custom/<skill>.toml` içindeki `workflow.on_complete` köprüsüyle atar (aşamalar `workflow.md` çapalarıyla koşar), WDS fazları ise kapanışı kendi SKILL.md'lerindeki `## On Complete` bölümünde taşır (kabuk dosyaların `on_complete` çapası yok). Hiçbiri metodoloji pozisyonu damgalamaz.
- **Yönlendirici personalar** (`bmad-agent-pm` / `-analyst` / `-architect` / `-ux-designer` / `-tech-writer`, altı `bmad-cis-agent-*` koçu, üç WDS personası `wds-agent-saga-analyst` / `-freya-ux` / `-mimir-builder` ve üç GDS danışman personası `gds-agent-game-architect` / `-designer` / `-tech-writer` — aynı persona şablonu: kimlik + menü + dispatch) oturumun ön kapısıdır: kimlik alır, tahtaya göre açılır ve kullanıcıyı bir workflow'a yönlendirir. Hiç post atmaz (dolayısıyla onlara da yönlendirme yapılmaz); zorunlu yarıları **okuma** tarafıdır — selamlamadan önce oryantasyon digest'i (röle pozisyonu *ve* bekleyen batonlar tek sınırlı çağrıda; digest çalışmazsa yedek yalnızca baton listesidir), menü genel bir liste değil *bu* projenin sıradaki gerçek adımı olsun diye. `check-handoff.py` her gerekçenin vaat ettiği okuma tarafını tek **sınırlı** çağrı olarak denetler: yönlendirici ve raporlayıcı digest ile, konuk katkıcı tek `read --context` ile. Eski iki çağrılık okuma (`read --context` + `handoffs`) artık hiçbir aktivasyonda geçemez — etiket tek başına yetmez.
- **Katkıcı (`bmad-advanced-elicitation`, `bmad-party-mode`, `bmad-eval-runner`)** — satır-içi teknik yükseltici: başka bir skill'in oturumu *içinde* çağrılır, çağıranın tahta bağlamını okur ve tam olarak bir `contribute` izi bırakır; diğer tüm yazımlar yasak (focus, run list ve kapanış çağıranın malı).
- **Raporlayıcı (`bmad-sprint-status`)** — proje durumunu raporlar, dolayısıyla yalnızca dosyadan rapor edemez: tahtayı okur ve bekleyen bir batonu, planın henüz bilmediği canlı iş olarak öne çıkarır.
- **Yardım yönlendiricisi (`bmad-help`)** — sıradaki skill'i önerir; bekleyen batonları da okur (salt-okunur) ve onları kataloğun önüne geçirir — bekleyen sinyal, haritanın henüz göstermediği canlı iştir.
- **Satır-içi (`bmad-review-*`, `bmad-editorial-review-*`, `bmad-loop-sweep`)** — bulgularını çağırana döndürür, hop atmaz; gözden geçiriciler çağıranın tahta bağlamını **okumaz** — bilgi asimetrisi tasarım gereğidir. `bmad-loop-sweep` yalnızca bmad-loop oturumu içinde çalışır, makine-okunur triajı orkestratöre döndürür.
- **İnsan kararı giriş noktaları (`bmad-checkpoint-preview`, `bmad-loop-resolve`)** — kullanıcı isteğiyle çağrılır ve bir insan kararını yönetir; karar vermeden önce tahtaya açılır (salt-okunur) — zincir bağlamı olmadan verilen karar boşluktan verilmiş karardır.
- **Araç (`bmad-agent-builder`, `bmad-document-project`, `bmad-generate-project-context`, `bmad-index-docs`, `bmad-shard-doc`, kurucular: `bmad-loop-setup`, `bmad-bmb-setup`, `bmad-module-builder`, `bmad-workflow-builder`, `bmad-customize`; WDS arka uçları: `memory`, `sync`; dikey doküman araçları: `gds-document-project`, `gds-generate-project-context`)** — zincir kaydı olmayan bir çıktı üretir (ajan skill'i, proje dokümanı, kurulu modül, ilerleme dosyası): ne hop ne tahta sözleşmesi. Dört doküman/bağlam skill'inin çıktısı (`project-context.md` vb.) birçok aşama tarafından *okunur* — ama baton olarak değil, aktivasyonda `persistent_facts` dosyası olarak: kayıt defteri bu bağımlılığı kimsenin tüketmeyeceği bir sinyal icat etmeden modeller.
- **Deprecated shim'ler** (`bmad-create-prd`, `bmad-validate-prd` → `bmad-prd`; `bmad-create-architecture` → `bmad-architecture`) kendi zincir mantığını taşımaz; `check-handoff.py` shim'in DEPRECATED olduğunu ve **zincir üyesi** bir skill'e yönlendirdiğini doğrular.

**Lint'in iki yapısal değişmezi (gerekçe etiketlerinin üstünde):** (1) *Bir hop alıcısı "bana kimse devretmiyor" diyemez* — `entry point`/`feeder`/`origin` etiketli bir alıcı kanalına hiç bakmaz, dolayısıyla bildirilen baton bayatlayana kadar bekler; ayrıca her alıcı kayıtlı bir üye olmalıdır (kayıtsız alıcıyı lint hiç denetlemez). (2) *Birden fazla aşamanın yazdığı run-key namespace'i imzalanmalıdır* — `story.` iki tarafından yazıldığı için dev kapanışı `--sender bmad-dev-story` taşır; imza olmadan önek atıfı create-story'yi krediler ve terminal hop bildirilmemiş bir çift gibi görünür. Terminal alıcılar da açıkça bildirilir (`CHAIN_TERMINAL`).

**Skill sözleşmesi (üreten skill'ler: PRD, UX, brief, mimari, brainstorming, forge, eval-runner):**

- **Focus (zorunlu):** Aktivasyonda `write --hot` ile tek state anahtarı; kilometre taşlarında value'yu tazele; kapanışta `hot --clear`.
- **Run list (thread çıkan her yerde zorunlu):** Açık soru/bekleyen mock/başarısız case `list-add --key <run-key>.pending`'e iş çıktıkça eklenir, çözülünce `list-remove`/`list-clear`. Kapanışta boş liste = bitmiş run; açık kalan = sonraki skill'e handoff sinyali.
- **Canvas (canlı resim gereken yerde):** `ux` yüzey haritası, `mimari` karar haritası, `brainstorming` fikir panosu, `eval-runner` tur yayı; cell'leri güncel tut, `watch` kaydet, kapanışta `canvas focus --clear` (canvas'ı downstream okusun diye ayakta bırak).
- **Kapanış kontrolü (tümü):** Focus+liste temizlendikten ama kendi handoff'unu atmadan önce `blackboard.py doctor --json` koş, `NEEDS ATTENTION`'ları kullanıcıya göster.

`custom/config.toml [hooks] blackboard = "on"|"off"` skill-tarafı kullanımı açar/kapar; motorun salt-okunur peek'i bundan bağımsızdır.

---

## 15. Komut Referansı

### 15.1 Dört slash komut (kullanıcı adına sen koşarsın)


| Komut                    | Sen ne yaparsın                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| ------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/metodoloji:init`       | `{project-root}` altına dizin+template kur. **Bir kez** çalışır ve `.metodoloji/initialized` marker'ını yazar; marker varsa kısa devre yapıp "already installed" der (yeniden kurmak için `--force`). Mevcutu ezmez. Manifestoları kopyalamaz. Gate key yoksa uyarır. Brownfield notu basar. Özet yazdırır (`/metodoloji:audit` sonraki adım).                                                                                                                                                                                                                  |
| `/metodoloji:gate-setup` | `~/.bmad/gate-key` varsa "already installed" deyip bitir (üzerine YAZMA). Yoksa `--init-secret` koş. 0600 kontrol et. İçeriği **asla** yazdırma/kopyalama/taşıma. Örnek `GATE-OK-...` çıktısı = tamam. Sonraki adım: ilk E kaydı.                                                                                                                                                                                                                                                                                                                               |
| `/metodoloji:verify`     | Tek kaydı doğrula: `python3 {metodoloji-root}/skills/bmad-research-experiment/scripts/run_experiment.py --verify --record {project-root}/docs/experiments/<id>.md`. `VERIFIED` = scope açık; `FORGED` = kaydı yeniden üret; `REJECTED` = hipotezi revize et; `ADVISORY-BLOCK` (exit 2) = yeni kayıtta yeniden ölç (kodu açmaz).                                                                                                                                                                                                                                 |
| `/metodoloji:audit`      | Sağlık kontrolü: (1) `sh {metodoloji-root}/scripts/check-plugin.sh` koş + özetle (exit 0 = HEALTHY). (2) Custom-odaklı rapor istenirse `scripts/check-custom.sh` (#0–#7). (3) `{project-root}/docs/experiments/` + `docs/development/` kayıtlarını listele, zincir boşluklarını not et (S var QR yok gibi). (4) Her E'ye `--verify` koş, VERIFIED/FORGED dağılımını raporla. (5) `custom/config.toml [hooks]` soft/hard değerlerini oku. (6) PASS/FAIL + düzeltme önerisi. Bridge şüphesinde `check-custom.sh --negtest` ile break→catch→restore kanıtı göster. |


### 15.2 Hızlı komut kartı

```bash
# Kurulum (init proje başına BİR kez; sonrası no-op)
/metodoloji:init
/metodoloji:gate-setup
/metodoloji:audit

# Deney
cp {metodoloji-root}/templates/_template_E.md docs/experiments/E-001.md
python3 {metodoloji-root}/skills/bmad-research-experiment/scripts/run_experiment.py \
  --record docs/experiments/E-001.md --run "python scripts/bench/bench.py" --dry-run
python3 {metodoloji-root}/skills/bmad-research-experiment/scripts/run_experiment.py \
  --record docs/experiments/E-001.md --run "python scripts/bench/bench.py"
python3 {metodoloji-root}/skills/bmad-research-experiment/scripts/run_experiment.py \
  --verify --record docs/experiments/E-001.md

# Kayıtlar
python3 scripts/create-methodology-record.py --story docs/development/stories/S-001.md
python3 scripts/create-qr-record.py --story docs/development/stories/S-001.md

# Denetim
python -m pytest -q
sh scripts/check-custom.sh
sh scripts/check-plugin.sh
sh scripts/check-methodology.sh
sh scripts/check-techdebt.sh
python scripts/check-handoff.py

# Customization
python3 hooks/engine/resolve_customization.py -s skills/bmad-dev-story -k workflow.activation_steps_append
python3 bmad/scripts/resolve_config.py --project-root . --key core.communication_language --key modules.tea.test_artifacts
python3 scripts/sync-hooks-json.py --write

# Blackboard
python3 bmad/scripts/blackboard.py write --key prd.acme --value "..." --type state --hot
python3 bmad/scripts/blackboard.py mirror --key prd.acme --value "PRD final" --to bmad-ux --note "..."
python3 bmad/scripts/blackboard.py mirror --key story.1-1 --value "dev complete" --to bmad-code-review --sender bmad-dev-story
python3 bmad/scripts/blackboard.py doctor

# Workflow (süreç motoru)
python3 bmad/scripts/workflow.py list --project-root .
python3 bmad/scripts/workflow.py next --project-root .
python3 bmad/scripts/workflow.py create --builtin seo-visibility --project-root .
python3 bmad/scripts/workflow.py complete --slug seo-visibility --stage analyze --project-root .
python3 bmad/scripts/workflow.py validate --spec docs/workflows/surec.json

# Log
tail -10 .metodoloji/logs/hook-audit.log
grep 'methodology_warnings' .metodoloji/logs/hook-audit.log
```

---

## 16. Denetim ve Sağlık Kontrolü

```bash
sh scripts/check-plugin.sh        # #0–#6f tam denetim (0=HEALTHY, 1=sorun)
sh scripts/check-plugin.sh --negtest
sh scripts/check-custom.sh        # bridge TOML #0–#7
python scripts/check-handoff.py   # handoff wiring lint
sh scripts/check-methodology.sh   # proje-kayıt formatı (kayıtsız tazede uyarılı exit 0)
sh scripts/check-techdebt.sh      # borç drift/ID/P0/orphan (#6b olarak da koşar)
```

**# haritası:** #0 gate key · #0b base config proje-adı sızıntısı · #1 motor selfcheck (birleşik `pre` + tekil modlar) · #1b hooks.json byte-identical · #2 manifesto wiring · #2b bridge runtime görünürlüğü (30 workflow + 3 agent-principles) · #2c bridge VERIFY (13 skill) · #3 approved envanter (VERIFIED/ADVISORY-BLOCK + CROSS-MACHINE uyarısı) · #3b code-scope coverage · #4 dokümantatif kayıtlar · #5 motor bütünlüğü (py\_compile) · #5b hard-gate geçerliliği · #5c custom/ statik · #6 development format · #6a `.env` · #6b tech-debt · #6c template kopya kimliği · #6d init marker bütünlüğü · #6d2 command→script referans bütünlüğü · #6d3 plugin sürüm tutarlılığı (`.plugin/plugin.json` ↔ `.plugin/marketplace.json` ↔ `.claude-plugin/plugin.json` ↔ `.claude-plugin/marketplace.json` ↔ `pyproject`) · #6e help catalog bütünlüğü · #6f trigger/description çakışması.

**Tek kayıt doğrulama + envanter:**

```bash
python3 {metodoloji-root}/skills/bmad-research-experiment/scripts/run_experiment.py \
  --verify --record docs/experiments/E-001.md
for f in docs/experiments/E-*.md; do
  python3 {metodoloji-root}/skills/bmad-research-experiment/scripts/run_experiment.py \
    --verify --record "$f"
done
```

---

## 17. Hata Ayıklama — Semptom → Senin Aksiyonun

Bir deny/uyarı gördüğünde kullanıcıya "sistem bozuk" deme: semptomu bul, nedeni doğrula, aksiyonu uygula, sonra raporla.


| Semptom                              | Neden                                                                            | Senin aksiyonun                                                                                                                            |
| ------------------------------------ | -------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| "No approved experiment record"      | Scope dışı yazım                                                                 | E aç → doldur → gate koş → VERIFIED                                                                                                        |
| "Gate key not configured"            | `~/.bmad/gate-key` yok                                                           | `--init-secret` (gate-setup yolu)                                                                                                          |
| Her şey DENY + exit 2                | Python/motor yolu bozuk                                                          | `python3 --version` (≥3.11), `ls hooks/engine/main.py modules/`, `python3 -c "import sys; sys.path.insert(0,'hooks/engine'); import main"` |
| "BRIDGE merge problem"               | TOML deep\_merge tutmadı / kök `customize.toml` yok                              | `resolve_customization.py -s ... -k workflow.activation_steps_append` çıktısında BRIDGE ara                                                |
| "Story experiment validation failed" | refs geçersiz / E yok / PENDING-REJECTED                                         | E varlığı + APPROVED + `--verify`                                                                                                          |
| ADVISORY-BLOCK                       | Küçük örneklem / n unknown / MISMATCH                                            | Yeni kayıtta büyük örneklem + eşleşen metrik (P10)                                                                                         |
| FORGED                               | Elle edit / cross-machine                                                        | Yeniden üret veya Re-Measured-By prosedürü (P11)                                                                                           |
| Stop raporu çift / garip             | Duplicate Stop kaydı                                                             | `/hooks` ile birini kaldır (stop zaten bloklamaz)                                                                                          |
| JSON parse hatası (Bash input)       | Async yarış / duplicate plugin                                                   | Senkron audit zaten devrede; duplicate kurulum + stale cache temizle + Claude'u güncelle                                                   |
| Commit deny (hard)                   | IR/QR/SP eksik                                                                   | Sırayla tamamla (IR→QR→SP); story metadata'yı düzelt                                                                                       |
| Deploy deny (hard)                   | IR/QR/SP/PR eksik                                                                | PR'ı tamamla (staging+rollback+monitoring+onay)                                                                                            |
| Bench reddedildi                     | free yüzeyde (scratch/tmp/temp/graft/openhands/*bmad/.metodoloji/kök explore*\*) | `scripts/bench/`'e taşı, yeni kayıtla koş                                                                                                  |
| hooks.json uyumsuzluğu               | Elle edit                                                                        | `sync-hooks-json.py --write`                                                                                                               |


**Kullanıcının sık sorduğu "neden"ler:**

- **"Soft/hard ne yapıyor?"** Motor çalışırken zincir eksikse: soft = uyar+geç, hard = DENY. Guard'ın `experiment_refs` kontrolü moddan bağımsız hep deny. Story metadata/zincir committe quality gate'e bağlı. Stop moddan bağımsız report-only.
- **"`scratch/`'a deneysiz kod yazabilir miyim?"** Evet — serbest; prototip/araştırma notu için. Üretime gidemez; bench'ler `scripts/bench/`'e terfi eder; secret kalıpları scratch'te de yasak.
- **"Anahtarımı paylaşabilir miyim?"** Hayır — makine-yerel, paylaşım HMAC'i öldürür.
- **"Paralel deney?"** Evet — her E kendi scope'unu açar, guard kapsayanı arar (P18).
- **"Eski `bmad-hooks.py`?"** Kaldırıldı — tekil dosya yok, modüler motor (`hooks/engine/main.py` + `modules/`) geçerli.
- **"TOML'dan öğe silme?"** Mekanizma yok — fork veya noop override.

---

## 18. ASLA Listesi — Atlanırsa Can Yakan 60 Detay

Bu 60 madde "iyi pratik" değil, sistemin mekanik sınırlarıdır. Her birini ihlal eden bir çağrı ya deny yer ya da sessizce kanıt üretir.

**E / Gate (1–15):**

1. İngilizce alan etiketleri zorunlu — Türkçeleştirme gate'i kör eder.
2. Gate-yazımlı alanları (`Decision`, `Gate Evidence`, `Next Step`, `Status` satırları, `Raw Results`, `Uncertainty`, `Metric`, `Measurement Command`) elle yazma.
3. `--measured` yok — sayıyı sen beyan edemezsin, gate parse eder.
4. Bench hiçbir free yüzeyde olamaz (`scratch/`, `tmp/`, `temp/`, `graft/`, `openhands/`, `_bmad/`, `.metodoloji/`, kök `explore_*`) — `scripts/bench/` gibi korumalı dizin kullan.
5. `Decision` yazılmış kaydı `--run` ile tekrar koşma — yeni kayıt aç.
6. Kontrol amaçlı `--run` koşma — karar yazar; önizleme için `--dry-run`.
7. `Code Scope: none` kod üretmeyen deney demektir — sonra kod yazarsan guard bloklar, şaşırma.
8. Scope glob'ları: `**` her derinlik, `*` tek segment, `?` tek karakter; virgül/boşlukla ayır.
9. Scope'u dar tut — geniş scope zayıf kanıt + büyük blast radius.
10. Tarih formatı E'de DD.MM.YYYY (S/IR/SP/QR/PR'de YYYY-MM-DD) — karıştırma.
11. Hipotez `H-NNN: "metrik >= eşik"` formatında, sayısal eşikli.
12. Ham verileri `docs/experiments/E-NNN/raw/` altında tut.
13. Token claim+measured+id+command'a bağlı — dördünden birini editlersen FORGED.
14. Legacy kayda `Measurement Command` ekleyip komutu değiştirmek downgrade = FORGED.
15. Cross-machine FORGED = provenans; `Re-Measured-By:` satırı + yerel yeni kayıt (token'ı elle yazma!).

**Story/Sprint (16–28):**

16. `experiment_refs` frontmatter'ı yazım anında denetlenir — mod ne olursa olsun PENDING/REJECTED = deny.
17. AC'nin 4 alt alanı (`Experiment/Type/Measured/Verify`) committe denetlenir — yazarken bloklanmaman erteleme ruhsatı değil.
18. `[HYPOTHESIS]` etiketli AC'de `Experiment: —` olabilir; diğerlerinde `experiment_refs` varken `Experiment` zorunlu.
19. Her task'ta `AC: AC-NNN` ve o AC mevcut olmalı (orphan yasak).
20. Her DoD maddesinde `DoD-NNN` (+ `Verify:`) olmalı.
21. Story `done` için QR kaydı şart; `review`/`done` için metodoloji (S) kaydı şart; `SP-NNN` referansı için SP kaydı şart.
22. Duplicate kayıt ID sadece yaratılışta taranır — mevcut dosyayı editlerken değil.
23. Terminal/heredoc ile story yazımında payload opak ise kontrol commit'e ertelenir (uyarıyı görmezden gelme).
24. Sprint hedefi tek cümle — iki cümlelik hedef scope kaçağıdır.
25. Kapasiteyi velocity'ye karşı kontrol et; borç eforunu toplama dahil et (X feature + Y borç = Z toplam).
26. P0/P1 borca hedef sprint zorunlu.
27. QR evi `docs/quality/` — yenileri oraya aç (legacy `docs/development/` kabul ama kanonik değil).
28. QR'daki AC/DoD tabloları standart formatta olmalı (`| AC | Status | Method | Evidence |`, `| DoD Item | Status | Evidence | Date |`) — denetim yapısal bakar.

**Hook/Güvenlik (29–45):**

29. Bilinmeyen uzantı korumalıdır — "bu uzantıyı tanımıyor, geçer" sanma; whitelist dışı = kod hedefi.
30. `package.json` exec-config sayılıp korunur; `tsconfig*.json`/lockfile'lar serbest — ayrımı ezberle.
31. `echo x > src/a.py` guard'a takılır (union matcher) — shell'den kaçış yok.
32. Komuttaki `$var` hedefleri düşürülür ama statik hedefler denetlenir — `cat $X > src/a.py` hâlâ yakalanır.
33. Secret taraması free-zone'dan ÖNCE koşar — scratch seni kurtarmaz.
34. Düz yazıda `load_secret` serbest, `load_secret(` deny — parantez/atama/köşeli arar.
35. Gate key'i terminalde `cat`leme, içeriğini dosyaya yazma — guard DENY atar (ve haklıdır).
36. Anahtarı paylaşma, repo'ya koyma, üzerine yazma (`--init-secret` mevcutu ezmez — zorlama).
37. Motor yoksa pre/guard DENY + exit 2 — "neden her şey bloklanıyor"un cevabı genelde Python/motor yolu.
38. Python 3.11 altı = `tomllib` yok = TOML merge ölür — sürümü kontrol et.
39. Config canlı okunur — TOML editinden sonra reload/restart gerekmez.
40. Timeout'lar tavan, bütçe değil (pre 10s, audit 2s, stop 5s; normal \~40ms).
41. Audit senkron + fail-open — yavaşlık şikayeti audit'ten değil, genelde duplicate hook kaydından.
42. "2 async PostToolUse hooks completed" görürsen duplicate kurulum var — manifest + `settings.json` + stale cache'i temizle.
43. Duplicate Stop ("Ran 2 stop hooks") `/hooks` ile temizlenir — stop zaten bloklamaz, ama raporu ikiler.
44. Bilinmeyen araç adı `unknown` → uyarı, deny değil — guard'ın bilmediği araç seni kitlemesin diye.
45. Arşiv limitleri (512MB/64MB/200k üye/2GB) — `tar/unzip` hedef taramasında bomba koruması var.

**TOML/Blackboard/Çıktı (46–60):**

46. Merge'de silme yok — noop override veya fork.
47. Dizi merge'ü `code`/`id`'ye bakar — anahtarsız diziler append olur (duplicate şişmesine dikkat).
48. Kişisel ayarlar `*.user.toml` (gitignored), takım ayarları commitli — tersi bilgi sızıntısı/çatışma üretir.
49. Legacy `config.yaml` proje TOML'una yenilir — migrate edip YAML'ı sil.
50. Aktivasyonlar config'i hedefli okur: `resolve_config.py --key <dotted.path>` (tekrarlanabilir). `--module tea` (23 satır / 818 B) yalnız eski kurulumlar için CLI şeklidir — modülün gördüğü flat görünümü verir ama çalıştırılacak biçim değil; controller `check_targeted_config_reads` + `check_no_module_dump_in_skills`.
51. Çıktı kuralı: kayıt/artefakt → `{project-root}`; template/config/script → `{metodoloji-root}`. Şüphede "dosya NE?" diye sor.
52. Manifesto projeye kopyalanmaz — stale `docs/bmad/` kopyasını sil.
53. `init` mevcutu ezmez — "template güncellenmedi" sanıyorsan sebebi bu; gerekiyorsa elle güncelle.
54. BRIDGE değişikliğini `resolve_customization.py -k workflow.activation_steps_append` ile doğrula — dosyada durması yetmez, runtime'da görünmeli.
55. Producer BRIDGE'de VERIFY (`ls -la`) atlanırsa #2c yakalar — kaydı yaratıp doğrulamadan geçme.
56. Motor hot path blackboard-free — hook kararları deterministik/hızlı; board'u hook'tan bekleme.
57. Skill sözleşmesi: aktivasyonda `write --hot`, kapanışta `hot --clear` + boş run list + `doctor --json`.
58. Handoff tüketimi handshake'tir — peek (`handoffs --skill`) bakmak, `consume` kapatmaktır; hook'lar tüketmez.
59. `read --context` kompakt özet verir (çağıranın odağını devralan konuk katkıcının tek sınırlı okuması) — tüm board dump'ını bekleme/isteme. Aktivasyonda onu `handoffs` ile ikili okumak yasaktır: röle pozisyonu ve bekleyen batonlar oryantasyon digest'inde birlikte gelir.
60. Değişiklik sırası: kodu değiştir → `pytest` (hangi kararın oynadığını test modülü söyler) → statik check'ler → bench+E ile metodolojik kayıt.

---

## 19. Kullanıcıya Raporlama Sözleşmesi

Kullanıcı mekanik ayrıntı istemez; **durumu ve sıradaki adımı** ister. Ama sistemi "çalışıyor/çalışmıyor" diye düzleştirmen de yasak.

**Her rapor şu 4 satırı içerir:**

1. **Ne yapıldı** — kayıt id'leriyle (E-045, S-027, QR-028...), "iyileştirdim" gibi belirsiz fiille değil.
2. **Kapı ne dedi** — `APPROVED`/`REJECTED`/`VERIFIED`/`FORGED`/`ADVISORY-BLOCK`, ve hangi modda (soft uyarı mı, hard deny mi).
3. **Sıradaki adım** — zincirin bir sonraki halkası (IR/SP/S/QR/PR) veya kullanıcıdan beklenen girdi.
4. **Açık kalan** — soft modda geçen bir eksik, bekleyen handoff, `doctor --json`'daki `NEEDS ATTENTION`.

**Söyleme biçimi:**

- **Deny'i açıkla, savunma yapma:** "Guard yazımı kesti çünkü `src/auth/**` için VERIFIED deney yok. E-003'ü açtım; ölçüm eşiği tutarsa yazıma devam ederim." Deny bir hata değil, sistemin çalışmasıdır.
- **Soft uyarıyı gizleme:** Uyarıyla geçtiysen "geçti ama şu eksikle geçti" de; hard moda alındığında aynı eksik bloklar.
- **Exit kodunu yorumla:** `--verify` exit `0` VERIFIED, `1` FORGED/REJECTED, `2` ADVISORY-BLOCK (token gerçek ama kod kapalı) — kullanıcıya bunu üç ayrı durum olarak anlat, "hata" diye özetleme.
- **Cross-machine FORGED'i suçlama olarak anlatma:** "Bu kayıt başka makinede imzalanmış; kendi anahtarımla yeniden ölçmem gerekiyor" — tasarım bu.
- **Bitmediyse "bitti" deme.** Yarım kalan halkayı ve nedenini söyle; sahte tamamlanma raporu, deny'den daha zararlıdır.

---

## 20. Referans — Mimari Harita, Kök Çözümleme, Sözlük

### 20.1 Mimari harita

```
metodoloji/
├── .plugin/plugin.json                  # plugin tanımı
├── .claude-plugin/marketplace.json      # defaultEnabled:false → opt-in
├── hooks/
│   ├── hooks.json                       # TEK birleşik hook manifestosu (iki runtime da bunu bulur, üretilir)
│   ├── engine/main.py                   # giriş (pre/guard/quality/deploy/audit/stop/session_start)
│   ├── engine/modules/                  # guard (guard+quality+deploy), audit, stop, plan, mirror, state, config, archive, bash_targets, blackboard, utils
│   ├── engine/resolve_customization.py  # ince re-export (gerçek: bmad/scripts/resolve_customization.py)
│   └── scripts/bootstrap.sh, hook-entry.sh, run-hook.sh
├── skills/                              # 125 BMAD skill (native gövde)
├── custom/                              # 121 TOML (33 BRIDGE aktif) + config.toml (soft/hard)
├── bmad/                                # modül verisi (bmm,cis,gds,wds,tea,core,bmb,loop,_config) + scripts/ (blackboard, workflow, orient, resolve_config, resolve_customization, skeleton)
├── templates/                           # _template_E/IR/SP/QR/PR/S/BD/C + README/tech-debt/scratch-README
├── commands/                            # /metodoloji:init, gate-setup, verify, audit (.md)
├── scripts/                             # check-plugin/custom/methodology/techdebt, check-handoff, create-*-record, sync-*, bench/
├── docs/                                # CLAUDE.md, bmad manifestoları, kayıt şablonları (experiments/development/quality/research/design) + native çıktılar (development/native/)
└── .metodoloji/                         # blackboard.json + logs/hook-audit.log + workflow/ (ilk init/audit koşusunda oluşur — repo'ya commitlenmez)
```

> Not: eski `bmad-output/` dizini legacy'dir — taşınmıştır, yazılmaz. Guard `docs/` + `bmad-output/` + `_bmad-output/` + `design-artifacts/` altında kayıt tarar (okuma fallback'i, bkz. `guard.py:_RECORD_SCAN_ROOTS`).

### 20.2 Kök çözümleme (ezberle)


| Placeholder         | Gerçek değer                                                                                                                                                                                                                                                                                                                                                                   |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `{project-root}`    | Hedef proje kökü (`$CLAUDE_PROJECT_DIR` / `$OPENHANDS_PROJECT_DIR`, yoksa cwd)                                                                                                                                                                                                                                                                                                 |
| `{metodoloji-root}` | Plugin kurulum kökü (sırayla: SessionStart `METODOLOJI active (plugin: PATH)` satırı — verbatim, arama yapma → `$CLAUDE_PLUGIN_ROOT` / `$METODOLOJI_PLUGIN_ROOT` varsa → sabit checkout `~/.claude/plugins/marketplaces/metodoloji/` → sürümlü cache `~/.claude/plugins/cache/metodoloji/metodoloji/*` (en yeni önce) → OpenHands `~/.openhands/plugins/installed/metodoloji`) |
| `{skill-root}`      | Plugin içindeki skill konumu                                                                                                                                                                                                                                                                                                                                                   |


**Alet-olarak-enstrüman kuralı:** Plugin scriptini (`run_experiment.py`, `resolve_customization.py`) `{metodoloji-root}`'tan *çalıştırırsın*; ürettiği kayıt yine `{project-root}`'a yazılır. Çıktının kökünü ne olduğu belirler, hangi script ürettiği değil.

**Manifesto kuralı:** `docs/bmad/*-methodology.md` dosyaları plugin-kanoniktir — projeye kopyalanmaz, pluginden okunur. Eski `init`'ten kalma `docs/bmad/` kopyası varsa sil (stale kafa karıştırır).

### 20.3 Sözlük


| Terim                                         | Anlam                                                                                  |
| --------------------------------------------- | -------------------------------------------------------------------------------------- |
| BMAD                                          | Build Methodology for Agent-Driven development                                         |
| E                                             | Experiment (Mode A, kantitatif). Koda giden TEK mekanik yol                            |
| IR                                            | Implementation Readiness (Gate 1)                                                      |
| SP                                            | Sprint Planning (Gate 2)                                                               |
| S                                             | Story (atomik iş birimi)                                                               |
| QR                                            | Quality Review (Gate 3, commit öncesi)                                                 |
| PR                                            | Production Readiness (Gate 4, deploy öncesi)                                           |
| Mode B / D                                    | Kalitatif / bağlamsal araştırma → `docs/research/` (dokümantatif, kodu açmaz)          |
| Mode C                                        | Tasarım → `docs/design/` (dokümantatif, kodu açmaz)                                    |
| Guard                                         | PreToolUse bekçisi — VERIFIED deney yoksa kod yazımını keser                           |
| Quality                                       | `git commit` zincir kontrolü (IR→QR→SP)                                                |
| Deploy                                        | Deploy komutu zincir kontrolü (IR→QR→SP→PR)                                            |
| Audit                                         | PostToolUse — her çağrıyı `.metodoloji/logs/hook-audit.log`'a JSON satırı olarak yazar |
| Stop                                          | Oturum kapanış raporu — **asla engellemez** (report-only)                              |
| BRIDGE                                        | Native skill çıktısını metodoloji kaydına bağlayan TOML adımı                          |
| VERIFIED / FORGED / REJECTED / ADVISORY-BLOCK | `--verify` çıktıları (bkz. #8.1)                                                       |
| Fail-closed / fail-open                       | Motor çalışamazsa: pre/guard DENY+exit 2; quality/deploy/audit/stop sessiz geç         |
| Soft / hard                                   | Motor çalışıyor ama zincir eksikse: soft=uyar+geç, hard=DENY                           |
| Free zone                                     | Guard denetimi dışı (`scratch/`, `docs/`, `tmp/`...)                                   |
| Code target                                   | Guard koruması altındaki dosya (whitelist sınıflandırma)                               |
| Gate key / token                              | `~/.bmad/gate-key` ve `GATE-OK-...` HMAC imzası                                        |
| Blackboard                                    | `.metodoloji/blackboard.json` — event-sourced çalışma bağlamı                          |


### 20.4 Bakım notu

Bu runbook `metodoloji` v0.1.0'ya göre yazıldı. Plugin evrildikçe şu dosyalarla çapraz kontrol et: `docs/CLAUDE.md` (motor özeti), `docs/bmad/*-methodology.md` (manifesto), `.plugin/plugin.json` + `.claude-plugin/*.json` + `pyproject.toml` (sürüm), `templates/` (güncel şablon), `scripts/` (komut adları). Lisans + BMAD atfı için `LICENSE` dosyasına bak.

