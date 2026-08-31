# Gate 2 — Implementación (cierre de Fase 03)

**Estado: 4 de 5 criterios cumplidos — 2026-08-30** · Pendiente **solo la revisión humana**.

Los cuatro criterios automatizables están en verde y verificados en CI. El quinto es una acción
que este proceso no puede firmarse a sí mismo: **la mitad humana del dual review**.

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
- [ ] **Dual review completado (humano + IA)**
      → **Pendiente de la mitad humana.** El código se escribió y revisó con asistencia de IA,
        con `bandit` y `pip-audit` como red automática. Falta que **Jeremi Alcala** revise el
        diff. Es el único criterio de este gate que no puede automatizarse, y marcarlo sin que
        ocurra vaciaría de sentido el control.
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

## Para cerrar el gate

Una sola cosa: **que Jeremi revise el código**. Cuando ocurra, marcar la casilla del dual
review, cambiar el estado de este documento a `SUPERADO` y cortar `0.5.0` en el `CHANGELOG.md`.
