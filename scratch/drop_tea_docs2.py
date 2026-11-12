"""Replace the last TEA examples in the runbooks with surviving modules."""
import pathlib

EDITS = {
    "GUIDE.md": [
        ('# {project-root}/bmad/config.toml (team — committed)\n[modules.tea]\nrisk_threshold = "p0"\n',
         '# {project-root}/bmad/config.toml (team — committed)\n[modules.bmm]\nplanning_artifacts = "{project-root}/docs/planning"\n'),
        ('python3 {metodoloji-root}/bmad/scripts/resolve_config.py --project-root . --module tea',
         'python3 {metodoloji-root}/bmad/scripts/resolve_config.py --project-root . --module bmm'),
        ('`--module tea` is 23 lines / 818 B while a targeted read is 5 lines / 142 B;',
         '`--module bmm` is 23 lines / 818 B while a targeted read is 5 lines / 142 B;'),
        ('--key modules.tea.test_artifacts',
         '--key modules.bmm.planning_artifacts'),
        ('- **Routing personas** (`bmad-tea` — the persona template: identity + menu + dispatch) are the session\'s front door: they take identity, open against the board and route the user into a workflow. They never post (so nothing routes to them either); their mandatory half is the **read** side — an orientation digest before greeting (relay position *and* pending batons in one bounded call; if the digest fails, the fallback is the baton list alone), so the menu is not a generic list but *this* project\'s real next step. `check-handoff.py` checks the read side each rationale promises as one **bounded** call: routing and reporting via the digest, guest contributors via a single `read --context`. The old two-call read (`read --context` + `handoffs`) can no longer appear in any activation — a label alone is not enough.\n',
         ''),
    ],
    "KILAVUZ.md": [
        ('# {project-root}/bmad/config.toml (team — committed)\n[modules.tea]\nrisk_threshold = "p0"\n',
         '# {project-root}/bmad/config.toml (team — committed)\n[modules.bmm]\nplanning_artifacts = "{project-root}/docs/planning"\n'),
        ('python3 {metodoloji-root}/bmad/scripts/resolve_config.py --project-root . --module tea',
         'python3 {metodoloji-root}/bmad/scripts/resolve_config.py --project-root . --module bmm'),
        ('`--module tea` 23 satır / 818 B iken hedefli okuma 5 satır / 142 B;',
         '`--module bmm` 23 satır / 818 B iken hedefli okuma 5 satır / 142 B;'),
        ('--key modules.tea.test_artifacts',
         '--key modules.bmm.planning_artifacts'),
        ('- **Yönlendirici personalar** (`bmad-tea` — persona şablonu: kimlik + menü + dispatch) oturumun ön kapısıdır: kimlik alır, tahtaya göre açılır ve kullanıcıyı bir workflow\'a yönlendirir. Hiç post atmaz (dolayısıyla onlara da yönlendirme yapılmaz); zorunlu yarıları **okuma** tarafıdır — selamlamadan önce oryantasyon digest\'i (röle pozisyonu *ve* bekleyen batonlar tek sınırlı çağrıda; digest çalışmazsa yedek yalnızca baton listesidir), menü genel bir liste değil *bu* projenin sıradaki gerçek adımı olsun diye. `check-handoff.py` her gerekçenin vaat ettiği okuma tarafını tek **sınırlı** çağrı olarak denetler: yönlendirici ve raporlayıcı digest ile, konuk katkıcı tek `read --context` ile. Eski iki çağrılık okuma (`read --context` + `handoffs`) artık hiçbir aktivasyonda geçemez — etiket tek başına yetmez.\n',
         ''),
    ],
}

for rel, pairs in EDITS.items():
    p = pathlib.Path(rel)
    s = p.read_text(encoding="utf-8")
    for old, new in pairs:
        if old not in s:
            print(f"SKIP {rel}: {old.splitlines()[0][:60]}")
            continue
        s = s.replace(old, new, 1)
        print(f"OK   {rel}")
    p.write_text(s, encoding="utf-8")
