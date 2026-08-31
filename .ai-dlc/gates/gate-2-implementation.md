# Gate 2 — Implementación (cierre de Fase 03)

**Estado: SUPERADO — 2026-08-30** · Versión cortada: `0.5.0`

Los cuatro criterios automatizables están en verde y verificados en CI, y la revisión humana
se completó al aprobar y mergear el PR #3.

- [x] **SAST sin findings críticos/altos**
      → `bandit -r app/ -ll` en el job `sast` de CI. **0 hallazgos** sobre 645 líneas, en
        ninguna severidad. Se analiza solo `app/`: en `tests/` los `assert` dispararían B101
        sin aportar nada.
- [x] **Dependencias verificadas (SCA) — sin deps alucinadas ni vulnerables (A03)**
      → `pip-audit -r requirements.txt` en el job `sca`: **sin vulnerabilidades conocidas**.
        Se auditan las dependencias de **producción**, no el entorno de desarrollo: una CVE en
        `pytest` no llega nunca al servidor.
        Las 4 dependencias directas están fijadas a versiones existentes, verificadas contra
        PyPI. **SBOM CycloneDX 1.6** generado y archivado como artefacto por ejecución
        (90 días de retención). Cierra **DS-05** y **D-04**.
- [x] **Cobertura ≥ 80 % branch**
      → **93,47 %**, con umbral automático `--cov-fail-under=80` en CI.
        Medida **solo con las pruebas rápidas**, a propósito: una puerta de calidad que
        dependa de que haya un Docker en marcha es frágil. Cierra **D-05**.
        Lo que queda sin cubrir es esencialmente `deployer._run`, la frontera con el
        subproceso de Docker, que sí ejercitan las pruebas e2e.
- [x] **Dual review completado (humano + IA)**
      → El código se escribió y revisó con asistencia de IA, con `bandit` y `pip-audit` como
        red automática. **Jeremi Alcala** revisó y aprobó el diff al mergear el PR #3
        (2026-08-30). Era el único criterio de este gate que no podía automatizarse.
- [x] **Sin secretos en el código**
      → `git ls-files` no lista `.env` ni `config/apps.yml`; `.gitignore` los excluye; el
        instalador genera el secreto en el servidor y no lo persiste en el repositorio.
        `config.load_settings` rechaza arrancar con un secreto de menos de 32 caracteres.

## Cómo se comprueba

Los cuatro criterios automáticos corren en cada push y pull request:

| Job de CI | Criterio | Comando |
|---|---|---|
| `test` | Cobertura ≥ 80 % branch | `pytest -m "not docker" --cov=app --cov-branch --cov-fail-under=80` |
| `sast` | SAST | `bandit -r app/ -ll` |
| `sca` | SCA | `pip-audit -r requirements.txt` |
| `sca` | SBOM | `cyclonedx-py requirements requirements.txt -o sbom.json` |

## Deuda que este gate deja abierta

Superarlo no significa que no quede trabajo; significa que lo que queda no bloquea avanzar:

- **D-06**: mutation testing sistemático. Solo se mutó a mano el camino del rollback.
- **D-07**: pruebas de contrato del OpenAPI frente a la implementación.
- **DS-02**: rotación del `.jsonl` de despliegues (Gate 4).
- **DS-03**: notificación del resultado del despliegue (Gate 4).
- **DS-06**: decidir si `health_url` pasa a ser obligatoria.
