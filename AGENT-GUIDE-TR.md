# AGENT-GUIDE-TR.md — metodoloji'yi Tamamen Prompt ile Kullanma Kılavuzu

> **Bu dosya nedir?** `metodoloji` eklentisi (v0.1.0, çift-runtime: OpenHands + Claude Code, BMad Method uyumlu) için prompt ile kullanım kılavuzudur. Komut çalıştırmaz, dosya açmaz, anahtara dokunmazsınız. **Prompt atarsınız**, gerisini agent yapar ve okunabilir bir dille raporlar. İngilizce karşılığı: [`AGENT-GUIDE.md`](AGENT-GUIDE.md).
> **Nasıl kullanılır:** Durumunuzu [prompt kataloğu](#9-prompt-kataloğu-dizini) veya [kombinasyon matrisinden](#6-kombinasyon-matrisi--proje-durumu--hedef--prompt) bulun, prompt'u kopyalayıp yapıştırın, altındaki takip prompt'larını izleyin.
> **Dil kuralı:** Prompt'lar **İngilizcedir** — çünkü komutlar, dosya adları, kayıt alan etiketleri ve statü belirteçleri İngilizce kalmak zorundadır (gate parse ediyor). Aynı niyeti Türkçe söyleyebilirsiniz; agent aynı eyleme bağlar.
> **Lisans:** MIT (`LICENSE`). Metodoloji BMAD-METHOD (BMad Code, MIT) üzerine kuruludur — atıf ve trademark notu `LICENSE` sonundaki `ATTRIBUTION` bölümündedir.

---

## İçindekiler

1. [Prompt ile İşleyiş](#1-prompt-ile-işleyiş)
2. [Oturum Başlangıç Prompt'u (oturum başına bir kez yapıştır)](#2-oturum-başlangıç-promptu-oturum-başına-bir-kez-yapıştır)
3. [Kurulum Prompt'ları — Proje Tipine Göre](#3-kurulum-promptları--proje-tipine-göre)
4. [Yapım Prompt'ları — Tüm Teslim Senaryoları](#4-yapım-promptları--tüm-teslim-senaryoları)
5. [Bakım Prompt'ları — Denetim, Sıkılaştırma, Güncelleme, Kurtarma](#5-bakım-promptları--denetim-sıkılaştırma-güncelleme-kurtarma)
6. [Kombinasyon Matrisi — Proje Durumu × Hedef → Prompt](#6-kombinasyon-matrisi--proje-durumu--hedef--prompt)
7. [Cevapları Okuma — Agent'ın Yanıtının Anlamı](#7-cevapları-okuma--agentın-yanıtının-anlamı)
8. [Sorun Giderme Prompt'ları — Semptom → Gönderilecek Prompt](#8-sorun-giderme-promptları--semptom--gönderilecek-prompt)
9. [Prompt Kataloğu Dizini](#9-prompt-kataloğu-dizini)
10. [Anti-Pattern — Başarısız Prompt'lar ve Doğru Yazımı](#10-anti-pattern--başarısız-promptlar-ve-doğru-yazımı)

---

## 1. Prompt ile İşleyiş

### 1.1 İş bölümü

| Siz yaparsınız | Agent yapar |
|---|---|
| Hedefi prompt ile tarif edersiniz | Skill'leri seçer, planlar, kayıt açar |
| Slash komutunu veya senaryo prompt'unu yapıştırırız | Çalıştırır ve sonucu raporlar |
| "Neden bloklandı?" diye sorarsınız | Gate'i okur, tam gerekçeyi söyler |
| "Yeterli" kararını siz verirsiniz | Her iddiayı "bitti" demeden önce mekanik doğrular |
| **Yapmazsınız:** shell komutu, dosya düzenleme, anahtarı okuma/yazdırma | Gate'leri koşar, kayıt yazar, kapsamı denetler, kontrolleri çalıştırır |

### 1.2 İki tür prompt

1. **Slash prompt'ları** — dört sabit komut. Harfiyen söyleyin; kurulum yüzeyi bunlardır:
   - `/metodoloji:init` — projenize `docs/` kayıt iskeletini kurar (bir kez; sonraki çağrılar no-op)
   - `/metodoloji:gate-setup` — makineye özel gate anahtarını üretir (makine başına bir kez; asla gösterilmez, asla paylaşılmaz)
   - `/metodoloji:verify` — tek deney kaydını doğrular: `VERIFIED` / `FORGED` / `REJECTED` / `ADVISORY-BLOCK`
   - `/metodoloji:audit` — tam sağlık raporu: eklenti bütünlüğü, bridge bağlantısı, kayıt zinciri boşlukları, gate modları
2. **Serbest prompt'lar** — doğal dil. Her işe yarayan şablon:

```
<ne istediğiniz> + <nerede durduğu> + <"bitti"nin anlamı>
```

Örnek: `Add JWT authentication under src/auth/. Done means tests pass and a QR record exists before commit.`

### 1.3 Sürdüğünüz zincir (siz bir sonraki halkayı prompt'larsınız, agent ispatlar)

```
E (deney, gate ölçer) → IR (hazır mıyız?) → SP (sprint planı) → S (story)
  → onaylı kapsam içinde kod → QR (kalite) → commit → PR (prod) → deploy
```

Kod izne bağlıdır: gate'in kendisi ölçüp imzaladığı bir deney kaydı. Bu yüzden "ik satır kod yaz sadece" diyen prompt bir gate cevabıyla döner — bkz. [#10](#10-anti-pattern--başarısız-promptlar-ve-doğru-yazımı).

### 1.4 Okuyabilmeniz gereken statü sözlüğü

| Şunu görürsünüz | Anlamı | Sıradaki sözünüz |
|---|---|---|
| `VERIFIED` | deney gerçek, kapsamı açık | "tamam, uygula" |
| `APPROVED` | gate ölçümü kabul etti | zincire devam |
| `REJECTED` | ölçüm eşiği tutmadı | "hipotezi revize et, yeni deney aç" |
| `ADVISORY-BLOCK` | token gerçek ama kod kapalı (örneklem küçük / metrik uyuşmuyor) | "örneği büyüt, metriği eşleştir, yeni kayıtta ölç" |
| `FORGED` | token kayıtla uyuşmuyor — elle düzenleme ya da başka makine | "yeniden üret" (asla "token'ı düzelt") |
| `DENY (hard)` | mod doğrudan engelliyor | eksik kaydı tamamla |
| `uyarı + geç (soft)` | mod, `methodology_warnings` ile geçirdi | yine de düzelt; hard modda deny yer |
| `report-only` (Stop hook) | oturum özeti — engel değil | engel gibi okuma; özet gibi oku |

Stop hook'u **report-only**dir: oturumu özetler (devam eden story'ler, bu pencerenin yazımları, bekleyen handoff) ve hiçbir şeyi engellemez.

### 1.5 İyi bir yanıt nasıl görünür

Agent'tan gelen her yanıt şunları içermelidir; biri eksikse prompt ile isteyin:

1. **Ne yapıldığı** — kayıt id'leriyle (`E-045`, `S-027`, `QR-028`), muğlak fiillerle değil.
2. **Gate'in ne dediği** — `APPROVED`/`VERIFIED`/… **ve mod** (uyarıyla mı, engelleyerek mi).
3. **Sıradaki adım** — zincirin bir sonraki halkası ya da sizden istediği girdi.
4. **Açıkta kalan** — soft moddaki eksikler, bekleyen handoff'lar, `NEEDS ATTENTION` kalemleri.

Dördünü de zorlayan takip prompt'u: `Give me the four-line report: what changed, what the gate said, next step, what's still open.`

---

## 2. Oturum Başlangıç Prompt'u (oturum başına bir kez yapıştır)

Herhangi bir işe başlamadan önce agent'ı doğru pozisyona getiren, tek başına yeterli prompt. Oturumun başına yapıştırın (veya projenizin kalıcı talimatu olun).

```
You are operating a project that has the metodoloji plugin installed
(OpenHands + Claude Code, v0.1.0). Work through prompts only — I will not
run commands or edit files myself.

Operating rules you must follow:
1. Writing code requires an approved, gate-measured experiment record
   (docs/experiments/E-NNN.md → APPROVED → VERIFIED). No record, no write.
   The only way past a block is to complete the record, never to route
   around it (another tool, $var obfuscation, mode downgrade, forged token).
2. Never print, copy, cat or share the gate key; never edit gate-written
   fields (Decision, Gate Evidence, Next Step, Raw Results, Uncertainty,
   Metric, Measurement Command, Status).
3. Records and artifacts go under my project's docs/; templates, config and
   scripts are read from the plugin root — never write into the plugin.
4. Report every step with: what changed (record ids) / what the gate said
   (including soft-vs-hard) / next step / what is still open. Never claim
   "done" before the gate has said so.
5. Start each session by reading the injected context (chain progress,
   record inventory, pending handoffs) and tell me in one line where we are.

First: report the current state (init marker, gate key status, chain
progress, mode: which gates are soft/hard), then wait for my goal.
```

**Uyarlama satırları (size uyanı ekleyin):**

- Boş/yeni proje: `Project type: greenfield. Put code_guard, quality_gate and deploy_guard in hard mode after setup.`
- Mevcut kod tabanı: `Project type: brownfield. Start with code_guard = "soft" only; tighten it after the first VERIFIED scope.`
- Eklentinin kendisi: `Project type: self-hosting (metodoloji repo). hooks/, scripts/, skills/, custom/ are free here; regenerate hooks.json with scripts/sync-hooks-json.py --write instead of editing it.`
- Windows + WSL: `I use both PowerShell and WSL homes. If tokens come back FORGED on the other side, tell me to re-sync the key — do not regenerate it twice.`

---

## 3. Kurulum Prompt'ları — Proje Tipine Göre

### 3.1 Boş proje (greenfield) — Claude Code

```
Install the metodoloji plugin:
/plugin marketplace add https://github.com/metodoloji/metodoloji
/plugin install metodoloji@metodoloji

Then in this project run /metodoloji:init, /metodoloji:gate-setup and
/metodoloji:audit, and set code_guard, quality_gate and deploy_guard to
"hard" in custom/config.toml. Finish with a health summary.
```

**OpenHands varyantı:**

```
Install the plugin with install_plugin("github:metodoloji/metodoloji"),
then run /metodoloji:init, /metodoloji:gate-setup and /metodoloji:audit in
this project, set all three gates to "hard", and give me a health summary.
```

**Beklenen yanıt:** init bir kez çalıştı (marker yazıldı), anahtar bir kez üretildi (yalnız durum, asla içerik), `check-plugin.sh` çıkış durumu, mod satırı raporlandı (üçü de hard) ve önerilen sonraki prompt (ilk deney).

**Takip:** `Open my first experiment for <module> with a narrow scope and tell me which bench script to write.`

### 3.2 Mevcut kod tabanı (brownfield)

```
Bring the methodology onto this existing repo:
1. Run /metodoloji:init (it must not overwrite my files) and /metodoloji:gate-setup.
2. Set ONLY code_guard = "soft" — leave quality_gate and deploy_guard alone.
3. Run /metodoloji:audit and list which modules are most active so we can
   pick the first experiment target.
Warn me before any setting that would affect other projects.
```

**Beklenen yanıt:** init'in dosyaları ezmediği teyidi, soft mod aktif, zincir boşluklarıyla audit ve aday ilk modül (örn. `src/billing/**`).

**Takip'ler:** `Open a narrow experiment for <module> only.` → doğrulandıktan sonra: `Now switch code_guard back to "hard".`

### 3.3 Eklentinin kendi deposu (self-hosting)

```
We are working inside the metodoloji repo itself. Tell me the rules for this
mode (which trees are free, how hooks.json must be regenerated), then run the
full check suite so I know the baseline before I change anything.
```

**Beklenen yanıt:** self-modification zonu anlatımı (`hooks/`, `scripts/`, `skills/`, `custom/` *burada* serbest), `hooks.json` yeniden üretme kuralı (`scripts/sync-hooks-json.py --write`), `pytest` + `check-plugin.sh` başlangıç değeri.

### 3.4 Ekip / çok makine

```
We are two developers on two machines. Set this project up for that:
each machine keeps its own gate key (never share one), personal settings go
to *.user.toml and team settings to committed TOML, and if I get FORGED on a
teammate's record explain the Re-Measured-By procedure instead of touching
the token. Then show me the blackboard view for handoffs.
```

**Beklenen yanıt:** anahtar politikası, config ayrımı, `handoffs`/`chain-health` çıktısı.

### 3.5 Spike / prototip (zincir istenmiyor)

```
I only want to try an idea — no records. Work in scratch/, and when
something stabilises tell me which bench to promote into scripts/bench/ and
what an experiment would look like if we kept it.
```

**Beklenen yanıt:** free zone teyidi (secret kalıpları yine yasak) + terfi planı.

### 3.6 Kurulumdan sonraki ilk iş ("başla" prompt'u)

```
Chain is set up. Start the first piece of work: pick the smallest useful
scope, open the experiment, run the gate, and stop before writing code until
you can show me VERIFIED.
```

**Beklenen yanıt:** `E-001` teori/hipotez/metrik/kapsamla dolu, `--dry-run` gösterimi, gate sonucu, ardından `--verify` çıktısı.

---

## 4. Yapım Prompt'ları — Tüm Teslim Senaryoları

Her blok: göndereceğiniz prompt, açtığı takip prompt'ları ve yanıtta ne olması gerektiği. Hepsi #2'deki başlangıç prompt'unu varsayar.

### 4.1 Fikirden üretime (tam zincir)

**Gönderin:**

```
Turn this idea into production: <one paragraph of the idea>.
Walk the chain end to end — idea pressure test, brainstorm, PRD, architecture,
readiness check, epics, story, experiment, implementation, QR, commit, PR —
and stop at each gate to show me the result before moving on.
```

**Sırasıyla takip'ler:** `Show me the PRD draft.` → `Show the architecture decision map and the draft Code Scope.` → `Are we ready? Run the readiness check and stop if anything is missing.` → `Open the experiment for the first scope.` → `The story is drafted — finalize it now (refs, AC set, tasks, DoD).` → `Implement it.` → `Open the QR before I commit.` → `Commit, then prepare the PR.`

**Her gate'te beklenen yanıt:** kayıt id'leri, gate sonucu, mod. Agent'ın saygı duyması gereken sıra tuzakları (uyarmazsanız siz söyleyin): deney story taslağından **SONRA**, finalizasyondan **ÖNCE**; hazır olma kontrolü **iki kez** (mimari ve deneyden sonra); mimari kapsam taslağını üretir, deney onu kilitler; reddedilen deney yalnızca **o** halkayı yeniler — hiçbir şey başa sarılmaz.

### 4.2 Mevcut projeye özellik ekleme

**Gönderin:**

```
Add <feature> under <path>. Open the experiment with a narrow scope covering
only <path>, run the gate, and do not write any code until you can show me
VERIFIED for that scope.
```

**Takip'ler:** `Dry-run the measurement first.` → `Good — now implement it inside the scope.` → `Story + QR before the commit.`
**Beklenen yanıt:** `Code Scope: <path>` içeren `E-NNN` → `VERIFIED` → kod → `S-NNN` → `QR-NNN` → commit `IR✓ QR✓ SP✓` ile denetlenir.

### 4.3 Bugfix / hotfix

**Gönderin:**

```
Hotfix <symptom> in <file or module>. Use a single-file narrow experiment with
a regression bench, then QR, commit and PR — the PR must include rollback and
a kill switch even though it is small. Do not skip any link because it is small.
```

**Beklenen yanıt:** dar `E`, `scripts/bench/` altında regresyon bench'i, düzeltme, test kanıtlı `QR`, rollback/kill-switch dolu PR.

### 4.4 İş sırasında araştırma sorusu çıktı

**Gönderin:**

```
Before we continue: we need to answer <question>. Choose the right mode
(A if measurable, else B/C/D), open the record, and tell me whether this
blocks code or is documentation only.
```

**Beklenen yanıt:** mod seçimi + **yalnız Mode A kod açar** beyanı; zincir kaldığı yerden sürer.

### 4.5 Sprint planlama

**Gönderin:**

```
Plan the sprint: goal in one sentence, stories with ids and points, capacity
against our velocity, tech-debt items time-boxed, and a plan for every
blocker. Reject the plan if any story has no S record.
```

**Beklenen yanıt:** kontrol listesi tatmin edilmiş `SP-NNN`; eksik olanlar yumuşatılmadan açıkça söylenir.

### 4.6 Story yazımı (zorunlu metaveriyle)

**Gönderin:**

```
Write the story for <goal>. Frontmatter must reference an APPROVED experiment,
every AC needs its four fields (Experiment, Type, Measured, Verify), every
task must point at an existing AC, and every DoD item needs DoD-NNN + Verify.
Show me the gaps before you tell me it's ready.
```

**Beklenen yanıt:** `S-NNN` + açık eksik listesi (orphan'lar, eksik alanlar) — commit gate'inin sonra yalanlayacağı bir "hazır" raporu asla.

### 4.7 Commit öncesi kalite incelemesi

**Gönderin:**

```
Open the QR for <story>: coverage, tests, lint, security scan, review — and
the two standard tables (AC and DoD). If a DoD row is still pending while the
story is done, fix the record first; do not commit yet.
```

**Beklenen yanıt:** `docs/quality/` içinde `QR-NNN`, iki tablo standart formatta ve ya `APPROVED` ya da açıkça başarısız satırlar. QR coherence commit'i engellerse çözüm: `python3 scripts/sync-story-qr.py --apply`.

### 4.8 Commit

**Gönderin:**

```
Commit this story with a conventional message including the story and
experiment ids. If the commit gate blocks, tell me which link is missing
(IR, QR, SP) and complete it rather than softening anything.
```

**Beklenen yanıt:** `git commit` sonucu, ya da zincir sırasındaki eksik halka `IR → QR → SP` (+ story metaverisi / QR coherence).

### 4.9 Deploy

**Gönderin:**

```
Prepare production: complete the PR (staging, rollback, monitoring, feature
flag, runbook, window, approval), then deploy. If the deploy gate blocks,
name the missing link.
```

**Beklenen yanıt:** dolu `PR-NNN` bölümleri → deploy komutu → gate `IR✓ QR✓ SP✓ PR✓` → sonrasında **Deploy Result** doldurulur (metrikler, varsa PM id).

> Unutmayın: `git push origin main` bir deploy sayılır; hard modda PR'sız push deny yer.

### 4.10 Paralel deneyler

**Gönderin:**

```
We are running <A> and <B> at the same time. Keep both experiments narrow and
non-overlapping, name the covering experiment in every commit and story, and
tell me if a file would fall under both scopes.
```

### 4.11 Teknik borcu kaydetme

**Gönderin:**

```
The review found this debt: <description>. Add it to tech-debt.md with a
priority (P0/P1 need a target sprint), put a matching TODO in the code, and
time-box it into the next sprint. Tell me when it's paid so it moves to the
Paid table.
```

### 4.12 Incident → post-mortem

**Gönderin:**

```
SEV1/SEV2 just happened: <what>. Within this session write the blameless
post-mortem (UTC timeline, impact, 5 whys, detection/response, lessons,
owned actions), turn any resulting debt into a TD record, and note the PM id
in the PR's Deploy Result.
```

### 4.13 Beyan edilen süreci koşturma (workflow motoru)

**Gönderin:**

```
Run the seo-visibility workflow for this project and take it stage by stage:
show me `next`, do the stage's work, only mark it complete with the required
evidence, and stop if evidence is missing instead of marking it done.
```

**Takip'ler:** `What is the current stage and the computed next one?` → `Block it — reason: <no access>.` → `Resume when access arrives.`
**Beklenen yanıt:** JSON tabanlı durum; kanıtsız `complete` reddi; `regressed` bayrağı tüketildiği için döngü tek kez koşar; kod yine deney zincirine bağlı kalır.

### 4.14 Ekip kuralı koyma (TOML)

**Gönderin:**

```
Add a team rule for <skill>: <the rule>. Put it in the committed custom layer,
then prove it is actually visible at runtime — a rule sitting in a file is not
enough. Personal preferences go in the gitignored *.user.toml instead.
```

**Beklenen yanıt:** yazılan dosya + `resolve_customization.py -k …` ile etkin değerin ispatı.

### 4.15 Blackboard üzerinden koordinasyon

**Gönderin:**

```
Put <focus> on the blackboard as hot, track open questions in the run list,
then hand off to <next skill> when this stage is done — and show me pending
handoffs and chain-health before you do.
```

**Beklenen yanıt:** odak kuruldu, run listesi dolu, `handoffs`/`chain-health`/`doctor --json` çıktısı; kapanışta odak temizlenir ve `NEEDS ATTENTION` size gösterilir.

### 4.16 Skill ailelerine giden prompt'lar

| Prompt'unuz | Agent'ın ulaştığı skill'ler |
|---|---|
| `Pressure-test this idea before we build anything.` | `bmad-forge-idea`, `bmad-brainstorming`, `bmad-prfaq` |
| `Write the PRD: goal, scope, NFRs, acceptance criteria.` | `bmad-prd` (shim'ler `bmad-create-prd`/`bmad-validate-prd` buraya yönlenir) |
| `Produce the architecture: decisions, module boundaries, draft Code Scope.` | `bmad-architecture` |
| `Design the UX for <flow>.` | `bmad-ux`, `wds-*` |
| `Turn this into epics and stories.` | `bmad-create-epics-and-stories`, `bmad-create-story` |
| `Implement this story.` | `bmad-dev-story` (tek: `bmad-quick-dev`, toplu: `bmad-dev-auto`) |
| `Review the code and record quality.` | `bmad-code-review`, `bmad-quality-record` |
| `Add tests / raise coverage / wire CI checks.` | `bmad-testarch-*`, `bmad-qa-generate-e2e-tests` |
| `Prepare production readiness.` | `bmad-production-readiness` |
| `We went off track — correct course.` | `bmad-correct-course`, `bmad-retrospective` |
| `Build a game:` | `gds-*` (brief → GDD → mimari → story → dev → test → playtest) |
| `Build a web/UX product:` | `wds-*` + `bmad-ux` |
| `Run this skill in a clean room and report transcript, time and tokens.` | `bmad-eval-runner` |

---

## 5. Bakım Prompt'ları — Denetim, Sıkılaştırma, Güncelleme, Kurtarma

### 5.1 Sağlık ve denetim

```
Run /metodoloji:audit and give me the four-line report: what passed, what
failed, what is open, and the one thing to fix first.
```

Derinlik için takip: `Run the negative tests too — prove the gates still catch breakage.` (cevap: `sh scripts/check-plugin.sh --negtest` ve `sh scripts/check-custom.sh --negtest`).

### 5.2 Tek kaydı doğrulama

```
/metodoloji:verify <record id>
```

Beklenen: `VERIFIED` / `FORGED` / `REJECTED` / `ADVISORY-BLOCK` + #7'deki sonraki adım.

### 5.3 Neredeyiz? (günlük prompt)

```
Where are we? Show chain progress, the record inventory, review/in-progress
stories, pending handoffs and anything NEEDS ATTENTION — from the board and
the files, not from memory.
```

### 5.4 Bu oturumda ne oldu?

```
Read the audit log for this session and summarise it: every write, every deny,
every methodology warning, and whether any of them are still unresolved.
```

(agent `.metodoloji/logs/hook-audit.log` dosyasını `session_start` ile `session_stop` işaretleri arasından okur; gövdeler yalnız önizlemedir.)

### 5.5 Gate'leri sıkılaştırma (soft → hard)

```
We now have a VERIFIED scope. Move code_guard to "hard" (and quality_gate /
deploy_guard to "hard" if the chain exists), then prove it with one out-of-
scope write attempt and show me the deny.
```

### 5.6 Eklentiyi güncelleme

```
Update the metodoloji plugin, rerun the test suite and the plugin audit, and
tell me if hooks.json drifted (it must be regenerated, never hand-edited).
```

### 5.7 Güvenlik hijyeni

```
Check .env hygiene: .env must not be tracked, .env.example must exist, and
.gitignore must cover .env. Also sweep the audit log for methodology_warnings.
```

### 5.8 Kurtarma prompt'ları (semptomu harfiyen gönderin)

| Gönderdiğiniz | Agent'ın yapması gereken |
|---|---|
| `Everything is denied with exit 2.` | Python ≥3.11 ve motor yolunu kontrol et (`hooks/engine/main.py` + `modules/`) — fail-closed: bozuk motor her şeyi engeller |
| `This record says FORGED.` | elle düzenleme mi, başka makine mi: yeniden üret **ya da** kendi anahtarınla yeni kayıtla `Re-Measured-By` prosedürü |
| `Tokens are FORGED only inside WSL (or only in PowerShell).` | iki ev arasında tek anahtarı senkronla — `--init-secret` iki kez asla |
| `init says already installed but there is no skeleton.` | bozuk init: tek seferlik onarım `python3 {metodoloji-root}/bmad/scripts/skeleton.py --install` |
| `The stop report appears twice.` | yinelenen Stop kaydı → `/hooks` ile birini kaldır |
| `Claude reports "2 async PostToolUse hooks completed".` | yinelenen eklenti kurulumu: manifest + manuel `settings.json` girişi + eski cache |
| `hooks.json does not match.` | `python3 scripts/sync-hooks-json.py --write` ile yeniden üret |
| `The commit is denied and I don't know why.` | zincir sırasındaki eksik halkayı söyle (`IR → QR → SP`) + story metaverisi / QR coherence, kaydı tamamla |
| `A bench was rejected.` | bench free yüzeydeydi → `scripts/bench/`'e taşı ve **yeni kayıtta** yeniden çalıştır |

---

## 6. Kombinasyon Matrisi — Proje Durumu × Hedef → Prompt

### 6.1 Ana ızgara

| Proje durumu ↓ / Hedef → | Kurulum | Yapım | Araştırma | Kalite | Deploy | Özelleştirme | Denetim |
|---|---|---|---|---|---|---|---|
| **Boş (greenfield)** | #3.1 | #4.1 veya #4.2 | #4.4 | #4.7 | #4.9 (8. mod satırından) | #4.14 | #5.1 |
| **Mevcut repo (brownfield)** | #3.2 | #4.2 (code_guard soft → ilk VERIFIED'tan sonra sıkılaştır) | #4.4 | #4.7 | #4.9 | #4.14 | #5.1 |
| **Plugin deposu (self-host)** | #3.3 | #4.2 (not: `hooks/`, `scripts/`, `skills/`, `custom/` burada serbest) | #4.4 | #4.7 | #4.9 | #4.14 (commitli plugin katmanı) | #5.1 + #5.6 |
| **Ekip / çok makine** | #3.4 | #4.2 + #4.10 | #4.4 | #4.7 | #4.9 | #4.14 (takım vs `*.user.toml`) | #5.1 + #5.3 |
| **Spike (zincir yok)** | #3.5 | `scratch/` içinde çalış, sonra terfi et | #4.4 | — | — | — | #5.1 |
| **Devam eden iş** | — | #4.2 / #4.6 | #4.4 | #4.7 / #4.8 | #4.9 | #4.14 / #4.15 | #5.3 / #5.4 |
| **Bozuk / engelli** | #5.8 | #5.8 | — | #4.7 (QR coherence düzeltmesi) | #5.8 | #5.8 | #5.1 |

### 6.2 Runtime boyutu (prompt değişiyor mu?)

| Şey | Claude Code | OpenHands |
|---|---|---|
| Kurulum kelimesi | `/plugin marketplace add …` + `/plugin install metodoloji@metodoloji` + `claude plugin enable metodoloji` | `install_plugin("github:yunusgungor/metodoloji")` |
| Slash prompt'lar | `/metodoloji:*` aynen çalışır | aynı prompt'lar, runtime'ın skill/yüzeyinden |
| Geri kalan her şey (serbest prompt'lar, takipler, raporlar) | aynı | aynı |

Kural: **kurulum prompt'u dışında hiçbir şey değişmez** — sonrası aynıdır çünkü agent kendi araç kelime dağını normalize eder.

### 6.3 Mod boyutu (aynı prompt ne üretir)

Aynı yapım prompt'u yapılandırmaya göre farklı davranır (`custom/config.toml [hooks]`):

| Prompt'un amacı | `code_guard = "soft"` | `code_guard = "hard"` |
|---|---|---|
| `add this feature` | yazım **uyarıyla** geçer — yine de iletmeniz gerekir | VERIFIED kapsamı yoksa engellenir |
| `commit it` | zincir boşlukları uyarı olarak çıkar | `IR → QR → SP` (+ story metaverisi, QR coherence) yoksa `DENY` |
| `deploy` | boşluklar uyarır | `IR → QR → SP → PR` yoksa `DENY` |
| `close the session` | Stop raporlar, asla engellemez | Stop raporlar, asla engellemez |

Oturum başına bir kez sorun: `Which gates are currently soft and which are hard?`

### 6.4 Zincir durumu boyutu (sıradaki prompt ne)

| Agent şunu raporlarsa… | Sıradaki prompt'unuz |
|---|---|
| deney taslağı hazır | `Dry-run it, then run the gate.` |
| `APPROVED` | `Verify it, then start coding inside the scope.` |
| `REJECTED` | `Revise the hypothesis and open a new record — do not edit the old one.` |
| `ADVISORY-BLOCK` | `Bigger sample, matching metric, re-measure in a new record.` |
| `FORGED` | `Regenerate it (or Re-Measured-By if it came from another machine).` |
| IR eksik | `Complete the readiness check — stop if something is missing.` |
| SP eksik | `Plan the sprint: one-sentence goal, stories, capacity, debt, blockers.` |
| story taslak | `Finalize it: refs, AC set, task↔AC, DoD.` |
| QR yok / pending DoD satırı | `Open the QR and clear the pending rows before commit.` |
| PR eksik | `Complete the PR: staging, rollback, monitoring, flag, window, approval.` |
| hepsi yeşil | `Commit, deploy, fill Deploy Result, then report the four lines.` |

---

## 7. Cevapları Okuma — Agent'ın Yanıtının Anlamı

### 7.1 Deney doğrulama — çıkış kodları

| Çıkış | Çıktı | Anlamı | Hamleniz |
|---|---|---|---|
| 0 | `VERIFIED` | gerçek onay, kapsam açık | `implement it` |
| 1 | `FORGED` | token ≠ kayıt (elle düzenleme ya da başka makinenin anahtarı) | `regenerate` / `Re-Measured-By` — asla "token'ı onar" |
| 1 | `REJECTED` / kararsız | gate reddetti (ya da hiç koşmadı) | `revise and open a new record` |
| 2 | `ADVISORY-BLOCK` | gerçek token, kod kapalı (küçük örneklem / `n unknown` / metrik `MISMATCH`) | `bigger sample, matching metric, new record` |

### 7.2 Kayıt statüleri

| Kayıt | Görebileceğiniz statüler | Sağlıklı hâl |
|---|---|---|
| E | `planned` → `APPROVED` / `REJECTED` (+ gate'in yazdığı satırlar) | `APPROVED` + `VERIFIED` |
| IR | `READY` / `INCOMPLETE` | sprintten önce `READY` |
| SP | `planned` / `in-progress` / `completed` / `cancelled` | sprint sırasında `in-progress` |
| Story | `backlog` / `sprint` / `in-progress` / `review` / `done` / `blocked` | `done` yalnız onaylı QR ile |
| QR | `in-review` / `APPROVED` / `REJECTED` / `REVISED` | commit'ten önce `APPROVED` |
| PR | `preparing` / `READY` / `WAITING` | deploy'dan önce `READY` |

### 7.3 Kanıt isteyin (desteksiz iddiayı kabul etmeyin)

```
Show me the evidence for that claim: the exact gate output, the record id,
and the file you verified. If you cannot, mark it as not done.
```

İşe yarayan kanıt prompt'ları:

- `Run /metodoloji:verify on every experiment and give me the VERIFIED/FORGED split.`
- `Show the QR tables (AC and DoD) you produced.`
- `Show the effective value of that customization, not the file it sits in.`
- `Run doctor --json and show me what needs attention.`
- `Which gate produced this answer, and in which mode?`

### 7.4 Uyarı vs engel

**Uyarı** (`methodology_warnings`, soft mod) agent'ın kayıtlı bir eksikle geçtiği anlamına gelir. Sorun: `List every methodology warning from this session and fix each one.` **Engel** (deny) artık tek yolun eksik kaydı tamamlamak olduğunu söyler.

---

## 8. Sorun Giderme Prompt'ları — Semptom → Gönderilecek Prompt

| Gözlemlediğiniz | Şunu gönderin |
|---|---|
| "Hiçbir şey yazılmıyor" | `Everything is denied with exit 2 — check Python and the engine path and report the cause.` |
| "No approved experiment record" | `Open a narrow experiment for that path and take it through the gate.` |
| "Gate key not configured" | `Run /metodoloji:gate-setup once and confirm the key status without showing me its content.` |
| `FORGED` kaydı | `Is this a hand edit or another machine? Apply the right fix — never rewrite the token.` |
| `ADVISORY-BLOCK` | `Increase the sample, match the metric, and re-measure in a new record.` |
| Commit engellendi | `Which link is missing — IR, QR, SP, story metadata, or QR coherence? Complete it.` |
| Deploy engellendi | `Which link is missing — IR, QR, SP or PR? Complete the PR and retry.` |
| Bench reddedildi | `Move the bench into scripts/bench/ and re-run it in a new record.` |
| `hooks.json` uyuşmazlığı | `Regenerate hooks.json with the sync script; do not hand-edit it.` |
| Çift Stop raporu | `There is a duplicate Stop registration — clean it up via /hooks.` |
| Çift async hook uyarısı | `Duplicate plugin install — clean manifest, settings.json and the stale cache.` |
| Done story'de `pending` DoD satırı | `Sync the QR result back into the story, then re-check coherence.` |
| Nerede olduğunuz belirsiz | `Where are we? Chain progress, inventory, pending handoffs, NEEDS ATTENTION.` |
| Gate cevabına itirazınız var | `Explain which field the gate parsed and why it reached that verdict.` |

---

## 9. Prompt Kataloğu Dizini

| ID | Prompt | Bölüm |
|---|---|---|
| BOOT | Oturum başlangıç (operasyon kuralları + durum raporu) | #2 |
| SETUP-GREEN-CLAUDE | Kurulum + init + gate-setup + audit + hard mod (Claude Code) | #3.1 |
| SETUP-GREEN-OH | Aynı (OpenHands) | #3.1 |
| SETUP-BROWN | Mevcut repo, yalnız `code_guard = "soft"` | #3.2 |
| SETUP-SELF | Plugin deposu self-hosting kuralları + başlangıç değeri | #3.3 |
| SETUP-TEAM | Çok makine anahtarları + config ayrımı + handoff'lar | #3.4 |
| SETUP-SPIKE | Yalnız scratch prototip, terfi planı | #3.5 |
| FIRST-EXPERIMENT | En küçük kapsam → gate → `VERIFIED`'de dur | #3.6 |
| BUILD-FULL | Fikirden üretime, her halkada gate | #4.1 |
| BUILD-FEATURE | Bir yola özellik ekleme | #4.2 |
| BUILD-HOTFIX | Tek dosyalık deney + rollback'li PR | #4.3 |
| BUILD-RESEARCH | İş ortasında mod seçimi | #4.4 |
| BUILD-SPRINT | Kontrol listeli sprint planı | #4.5 |
| BUILD-STORY | Zorunlu metaverili story | #4.6 |
| BUILD-QR | Commit öncesi QR + standart tablolar | #4.7 |
| BUILD-COMMIT | Commit, engeli açıkla | #4.8 |
| BUILD-DEPLOY | PR bütünlüğü → deploy → Deploy Result | #4.9 |
| BUILD-PARALLEL | Kesişmeyen iki deney | #4.10 |
| BUILD-DEBT | Tech-debt satırı + TODO + sprint kalemi | #4.11 |
| BUILD-PM | Suçsuz post-mortem | #4.12 |
| BUILD-WORKFLOW | Beyan edilen süreç, kanıt zorunlu aşamalar | #4.13 |
| BUILD-TOML | Ekip kuralı + runtime ispatı | #4.14 |
| BUILD-BOARD | Odak, run list, handoff | #4.15 |
| SKILL-MAP | Hangi prompt hangi skill ailesine gider | #4.16 |
| M-AUDIT | Tam sağlık raporu | #5.1 |
| M-VERIFY | Tek kaydı doğrula | #5.2 |
| M-WHERE | Günlük durum prompt'u | #5.3 |
| M-LOG | Oturum audit log özeti | #5.4 |
| M-TIGHTEN | Soft → hard, deny'yi ispatla | #5.5 |
| M-UPDATE | Eklenti güncellemesi + drift kontrolü | #5.6 |
| M-ENV | `.env` hijyeni + uyarı taraması | #5.7 |
| RECOVER-* | Semptom bazlı kurtarma prompt'ları | #5.8 |
| GRID | Proje durumu × hedef → prompt | #6.1 |
| NEXT | Zincir durumu → sıradaki prompt | #6.4 |
| PROOF | "İspatla" prompt'u | #7.3 |
| TROUBLE-* | Semptom → prompt | #8 |

---

## 10. Anti-Pattern — Başarısız Prompt'lar ve Doğru Yazımı

| Bunu göndermeyin | Neden başarısız | Şunu gönderin |
|---|---|---|
| `Just write the code, it's two lines.` | kapsamı açan VERIFIED yoksa guard deny eder | `Open a narrow experiment for that file, run the gate, and only then write it.` |
| `Show me / share the gate key.` | anahtar referansları bilerek engellenir | `Check the key status only; fix problems with /metodoloji:gate-setup.` |
| `Mark the record APPROVED manually.` | gate'in yazdığı alana dokunmak `FORGED` üretir | `Re-run the measurement properly, or open a new record.` |
| `Run the gate without --dry-run so I can see the format.` | kararı bağlar | `Preview with --dry-run; only run it for real when we mean to decide.` |
| `Skip the QR, we're in a hurry.` | hard modda commit gate zaten deny eder | `Open the QR now — even a small one — then commit.` |
| `Leave all gates on soft permanently.` | uyarılar sahte yeşil birikir | `Start soft (brownfield), tighten to hard at the first VERIFIED scope.` |
| `Delete this item from the TOML.` | merge'de silme mekanizması yok | `Override it with the same code/id and a noop description, or fork the skill.` |
| `Put the bench in scratch/ so it's quick.` | gate free yüzeyden ölçüm koşmaz | `Put it in scripts/bench/ before we measure.` |
| `Edit hooks.json directly.` | üretilen dosya, byte-identical kontrolü | `Regenerate it with scripts/sync-hooks-json.py --write.` |
| `Copy the manifestos into the project.` | bayat kopyalar kafa karıştırır | `Read them from the plugin; delete any leftover docs/bmad/ copy.` |
| `Mark the workflow stage complete — I'm sure it works.` | kanıtsız `complete` reddedilir | `Attach the evidence (artifact/command/note), then complete it.` |
| `Report it as done and we'll verify later.` | sahte tamamlanma deny'den kötüdür | `Report: done / not done, with the gate output for each claim.` |
| `Set the scope to the whole codebase.` | geniş kapsam = zayıf kanıt + yüksek `FORGED` riski | `One module per experiment; keep the first scope deliberately small.` |

---

**Kapanış notu.** Bu dosya `metodoloji` v0.1.0 için yazılmıştır. Eklenti evrildikçe sözlerini `docs/CLAUDE.md`, `templates/` ve `.plugin/plugin.json` ile çapraz kontrol et. Lisans + BMAD atfı: `LICENSE`. İngilizce karşılığı: [`AGENT-GUIDE.md`](AGENT-GUIDE.md).

