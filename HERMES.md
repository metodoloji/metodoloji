# Hermes Agent üzerinde Metodoloji

Metodoloji'nin Hermes Agent portu. Aynı kayıt zinciri (E → IR → SP → S → QR → PR),
aynı mekanik kapılar, aynı 129 skill — tek fark, Hermes'in kendi plugin
sözleşmesine bağlanması: kapı **gateway süreci içinde**, `pre_tool_call`
üzerinden çalışır ve tool çağrısını gerçekten bloklar.

```
plugin.yaml          Hermes manifesti (ad, sürüm, sağlanan tool/hook/komutlar)
__init__.py          register(ctx) — tool + hook + skill + komut kaydı
hermes_adapter.py    iki diyalekt arasındaki tek köprü (motoru alt süreçle çağırır)
hermes_tools.py      5 deterministik tool (orient / init / verify / key / audit)
```

## Kurulum

```bash
# 1) Plugin'i kur (katalogdan veya Git'ten)
hermes plugins install metodoloji/metodoloji
# ya da elle: repo'yu ~/.hermes/plugins/metodoloji/ altına koy

# 2) Etkinleştir (portable paketler devre dışı kurulur)
hermes plugins enable metodoloji

# 3) Kapıların yüklendiğini doğrula
hermes plugins doctor ~/.hermes/plugins/metodoloji
#   → registrations: 5 tool(s), 6 hook(s)

# 4) Yeni oturum / gateway yeniden başlatma (plugin'ler süreç açılışında keşfedilir)
```

`hermes plugins enable` ayrıca `metodoloji` toolset'ini `platform_toolsets`'e
ekler; yani araçlar bir sonraki oturumda görünür. Tool Search açıksa plugin
araçları `tool_search` / `tool_call` arkasından gelir (Hermes'in tüm plugin
araçları için geçerli davranışı).

Gereksinimler: Hermes Agent (plugin + `pre_tool_call` API'si), Python 3.11+
(motor `tomllib` kullanır), POSIX `sh` (Windows'ta Git Bash yeterli), Git 2.x.

> Proje içindeki `./.hermes/plugins/` klasörü gateway tarafından **yüklenmez**.
> Plugin kullanıcı dizinine kurulmalıdır.

## Ne kaydeder

| Tür | Ad | Ne yapar |
|---|---|---|
| tool | `metodoloji_orient` | Salt-okunur durum özeti: kayıt envanteri, iskelet, gate key, board, bekleyen hand-off'lar |
| tool | `metodoloji_init` | Kayıt iskeletini kurar (idempotent, mevcut dosyayı ezmez) |
| tool | `metodoloji_verify` | Bir kaydın HMAC kapısını doğrular / gate key durumu / belgesel kayıt denetimi |
| tool | `metodoloji_key` | Gate key durumu ve makine başına bir kez oluşturma (`action=status|init`) |
| tool | `metodoloji_audit` | Sağlık kontrolü: `records` (hızlı) veya `full` (`check-plugin.sh`) |
| hook | `pre_tool_call` | `write_file` / `patch` / `multi_edit` / `notebook_edit` / `terminal` / `execute_code` → motor `pre` (guard + quality + deploy), **bloklayabilir** |
| hook | `post_tool_call` | Motor `audit` — redakte edilmiş denetim satırı (asla bloklamaz) |
| hook | `pre_llm_call` | İlk turda oturum bağlamını enjekte eder (sistem prompt'una değil, kullanıcı mesajına — prompt cache bozulmaz) |
| hook | `on_session_start` | Oturum durumunu sıfırlar |
| hook | `on_session_end` | Motor `stop` — oturum raporu `.metodoloji/logs/hermes-stop.log`'a yazılır |
| hook | `pre_verify` | `nudge_on_stop` kapalıyken no-op; açıkken açık işi tek sınırlı "devam et" turuna çevirir (Claude `Stop` karşılığı) |
| skill | `metodoloji:<ad>` | Repodaki 129 skill — hem `skill_view("metodoloji:bmad-brainstorming")` hem `/metodoloji:bmad-brainstorming` slash komutu |
| komut | `/metodoloji` | Durum özeti |
| komut | `/metodoloji-init` | İskeleti kur (`--force` destekler) |
| komut | `/metodoloji-gate-setup` | Gate key'i bir kez oluştur (varsa yeniden üretmez) |
| komut | `/metodoloji-verify <kayıt>` | Tek kaydı doğrula |
| komut | `/metodoloji-audit [full]` | Sağlık kontrolü |
| komut | `/metodoloji-add <kaynak>` | Repository/docs URL'si veya yerel PDF/Markdown için external-context taslağı hazırlar; ajan analizi tamamlayıp `finalize` eder |

Hermes'te slash komutu işleyicisi metni **kullanıcıya** döndürür, modele değil —
bu yüzden `commands/*.md` prosedürleri skill olarak da kaydedilir
(`metodoloji:init`, `metodoloji:audit`, `metodoloji:verify`,
`metodoloji:gate-setup`). Ajan bu prosedürleri `skill_view` ile okur.

## Kullanım akışı

```
/metodoloji                     # neredeyiz?
/metodoloji-init                # iskeleti kur (proje başına bir kez)
... çalışma: ajan E kaydı açar, ölçer, APPROVED alır ...
/metodoloji-verify docs/experiments/E-001.md
/metodoloji-audit               # zincir boşlukları + gate durumu
/metodoloji-add <kaynak>        # external-context taslağı + ajan analizi talimatı
```

Kapı reddettiğinde gerekçe tool sonucunda modele döner. Ürün kodu doğrulanmış
deney kapsamı ister; ölçüm tezgâhı kararsız bir kayıt altında aşamalanabilir;
ölçülebilir iddia taşımayan yapısal işler ise gerekçeli yapısal beyanla
tanımlanır. Ajan reddin nedenine göre kaydı, kapsamı veya yapısal beyanı düzeltir.

## Diğer runtime'larla eşleme

| Kavram | Claude Code | OpenHands | OpenCode | **Hermes** |
|---|---|---|---|---|
| Yazma aracı | `Write` / `Edit` | `file_editor` | `write` / `edit` / `patch` | `write_file` / `patch` / `multi_edit` |
| Kabuk | `Bash` / `PowerShell` | `terminal` | `bash` / `shell` | `terminal` |
| Kod yürütme | — | — | — | `execute_code` (yazma hedefleri kaynaktan çıkarılır) |
| Kapı hook'u | `PreToolUse` (deny) | `PreToolUse` (deny) | `tool.execute.before` | `pre_tool_call` → `{"action": "block"}` |
| Oturum bağlamı | `SessionStart` | `SessionStart` | session hook | `pre_llm_call` (ilk tur) |
| Kapanış raporu | `Stop` | `Stop` | — | `on_session_end` |

Motor tarafında `--runtime=hermes` dalı (`hooks/engine/modules/utils.py`) tool
adlarını iç sözleşmeye çevirir. `patch` adı OpenCode ile ortak olduğu için bu
dal **runtime'a sabitlenmiştir**, isim tahminine bırakılmaz.

## Hata politikası (fail-closed nerede?)

Bu hook'lar Hermes'in araç çağrılarına uygulanır; Metodoloji işletim sistemi
seviyesinde bir sandbox değildir. `computer_use` varsayılan olarak dosya
kapısının dışında kalır; çalışma-anında çözülemeyen `execute_code` yolları da
ayar `deny` yapılmadıkça uyarı verip devam edebilir. Diğer runtime'larla ortak
karar mantığı ve kapsama sınırları için
[runtime güvenlik kapsamı tablosuna](docs/runtime-security-coverage.md) bakın.

- `pre` (guard + quality + deploy): motor yoksa, çökerse veya 12 saniyede
  yanıt vermezse çağrı **bloklanır**. Motorun kendi politikasıyla aynı.
- `audit` / `stop` / `session_start`: rapor-only; hata oturumu kilitlemez.
- Motor **asla import edilmez**, her zaman alt süreç: bir kapı modülündeki
  `sys.exit`, bozuk bir kayıt veya takılan `git` gateway'i düşüremez.

## Onaylı akış — gerçek oturumda doğrulandı

```
1) docs/experiments/E-00N.md taslağı     → docs/ serbest, yazılır
2) scripts/bench/<ölçüm>.py              → "staging": karar verilmemiş kayıt varsa yazılır
3) run_experiment.py --record … --run …  → APPROVED + GATE-OK token
4) src/… yazımı                          → kapı açılır
```

Bu sıra iki canlı Hermes oturumunda uçtan uca çalıştırıldı:

| Kayıt | Ölçüm | Sonuç |
|---|---|---|
| E-001 | 15/15, eşik 0.90 | Gate **APPROVED**, ama guard **ADVISORY-BLOCK**: n=15 için %95 Wilson alt sınırı 0.80 < 0.90 → kod kapalı kaldı |
| E-002 | 60/60, eşik 0.90 | Wilson alt sınırı 0.94 ≥ 0.90 → yazım açıldı, `src/app.py` gerçekten oluştu |

Ders: *onay* tek başına yeterli değil — küçük örneklem kodu açmaz (metodolojinin
4. kuralı, Hermes portuna özgü değil). Bench dosyası, ancak Code Scope'u onu
kapsayan **karar verilmemiş** bir kayıt varsa yazılabilir (`scripts/bench/**`
staging alanı); ürün kodu her zaman VERIFIED kayıt ister.

## Canlı testte bulunup düzeltilenler

| Belirti | Kök neden | Düzeltme |
|---|---|---|
| `/metodoloji` → "No module named 'hermes_tools'" | Komut işleyicileri paket içinde mutlak import kullanıyordu | İşleyiciler modül başında relative import ile bağlanır |
| Kapanış raporu, **engellenen** yazımı "code written" sayıyordu | Bloklanan çağrı için de `post_tool_call` tetikleniyor ve denetim izine yazılıyordu | `tool_call_id` bazlı blok takibi: engellenen çağrı denetim izine girmez |
| Onaylı kayıt varken yazım "received no input — fail-closed blocked" | `guard.py`'de `fr` (freshness modülü) import edilmeden kullanılıyordu → `NameError` → fail-closed sarmalayıcı | Yerel `from . import freshness as fr` + regresyon testi (düzeltme geri alınınca test aynı satırda düşüyor) |
| Kapsam dışı yazım, ilgisiz bir ADVISORY kaydını gerekçe gösteriyordu | `find_approved`, kapsamı uyuşmayan kayıtlardan da `advisory` topluyordu | Doğru gerekçe ("scope not matched") artık advisory'den önce gelir |

## Hermes'e özgü ayarlar

Hermes'in kancaları Claude'unkinden farklı olduğu için beş davranış ayarlanabilir
(`hermes config set plugins.entries.metodoloji.settings.<anahtar> <değer>`,
sonra yeni oturum):

| Anahtar | Varsayılan | Ne yapar |
|---|---|---|
| `execute_code_guard` | `warn` | `deny`: dosya yolu sabit değilse `execute_code` yazımını bloklar (varsayılan: uyarı) |
| `block_gui_tools` | `false` | `true`: `computer_use` tamamen reddedilir (GUI, dosya kancasını görmez) |
| `carry_stop_report` | `true` | Önceki oturumun `stop` raporunu bir sonraki oturum başında bir kez modele verir |
| `nudge_on_stop` | `false` | `pre_verify` ile açık iş varsa tek seferlik "devam et" dürtmesi (Claude `Stop` benzeri) |
| `include_skill_triggers` | `true` | Skill'in `triggers:` sözcüklerini kayıtlı açıklamaya ekler (Hermes yönlendirmeyi açıklamadan yapar) |

## Hermes farkları — çözüm eşlemesi

| Fark | Çözüm |
|---|---|
| `Stop` kancası yok | `on_session_end` raporu yazar; bir sonraki oturum başında **bir kez** modele verilir (SHA-256 işaretiyle aynı rapor tekrarlanmaz). İstenirse `nudge_on_stop` ile `pre_verify` üzerinden tek seferlik dürtme |
| `execute_code` değişken yollu yazma | AST + sabit katlama: `p='src/a.py'; open(p,'w')`, `os.path.join`, `Path(...).write_text`, `shutil.copy/move`, `os.rename`, `to_csv/savefig` çözülür. Yalnızca çalışma anında belirlenen yollar (input/env/argv) çözülemez → uyarı veya `execute_code_guard=deny` |
| `process_manage` (PTY stdin) | `write`/`submit` eylemleri `terminal` olarak kapıya girer; `list/poll/log/wait` serbest |
| `computer_use` (GUI) | Dosya düzeyinde kapılanamaz; `block_gui_tools=true` ile tamamen reddedilir, varsayılanda dokunulmaz |
| `triggers:` yönlendirmesi | İlk 3 tetik sözcüğü kayıtlı açıklamaya eklenir; ayrıca `/metodoloji:<skill>` slash komutu |

## Bilinen sınırlar

- **`execute_code` çalışma-anı yolları.** AST, sabit atamaları, `os.path.join`,
  `Path(...)` ve kaydedici çağrıları çözer; yalnızca `input()`/env/argv'den gelen
  yollar çözülemez. Varsayılan uyarıdır; `execute_code_guard=deny` ile bloklanır.
- **`Stop` kancası yok.** Rapor `on_session_end`'de yazılır ve bir sonraki
  oturumun başında bir kez modele verilir (aynı rapor tekrarlanmaz). `pre_verify`
  ile sürekli dürtme yerine bu, metodolojinin "kapanışı asla kilitleme"
  kuralını korur; `nudge_on_stop=true` isteğe bağlı olarak Claude `Stop`
  davranışına yaklaşır (Hermes 3 dürtmeyle sınırlar).
- **Skill sayısı.** 129 skill'in açıklaması sistem prompt'undaki
  `<available_skills>` listesine girer. Yalnızca kapıları istiyorsanız:
  `METODOLOJI_HERMES_SKIP_SKILLS=1`.
- **`hermes plugins validate` güvenlik taraması.** İşlevsel kontrollerin tümü
  geçer; içerik tarayıcısı skill referanslarındaki *doküman örneklerini*
  (curl örneği, `eval` snippet'i, prompt-injection SSS örneği) işaretler. Bu
  yalnızca katalog başvurusu için engeldir, çalışmayı etkilemez.
- **Scope coverage uyarısı.** Bu portun kendi dosyaları (`hermes_adapter.py`,
  `hermes_tools.py`) metodoloji disiplinine göre onaylı bir deney kapsamında
  değil; `check-plugin.sh` bunu "outside every approved Code Scope" olarak
  raporlar. Yeni kod için E kaydı açılması beklenir — aracın kendi kuralı.

## Test

```bash
uv run --no-project --with pytest python -m pytest hooks/engine/tests/test_hermes_runtime.py -q
```

36 test: tool sözlüğü eşlemesi, `execute_code` hedef çıkarımı (yanlış pozitif
dahil), adaptörün blok/fail-closed davranışı, engellenen çağrının denetim izine
girmemesi, komut işleyicilerinin paket importu ve manifest sürüm eşitliği.
Onaylı-yazım `NameError` regresyonu `hooks/engine/tests/test_guard.py` içindedir.
