# Clasificación de Datos

* **Estado:** approved
* **Fecha:** 2026-08-30
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 00-project
* **Versión:** 0.1.0
* **Owner de datos (DPO):** Jeremi Alcala
* **Regulación aplicable:** Ninguna. El sistema **no trata datos personales**: los únicos
  identificadores son nombres de repositorio, de workflow y hashes de commit, todos públicos
  por definición al ser el repositorio público. No aplican GDPR ni PCI-DSS.

## Niveles

| Nivel | Definición operativa |
|---|---|
| **Público** | Ya es visible para cualquiera en Internet. Su divulgación no cambia nada. |
| **Interno** | No es secreto, pero publicarlo facilita el trabajo de un atacante. |
| **Confidencial** | Su divulgación permite comprometer el sistema o el host. |

## Inventario de datos

| Dato | Nivel | Dónde vive | En reposo | En tránsito | Retención |
|---|---|---|---|---|---|
| **Secreto del webhook** (`WEBHOOK_SECRET`) | **Confidencial** | `/etc/cd-receiver/receiver.env` y GitHub Secrets del webhook | Fichero `0600` `root:root`, fuera del repositorio | Nunca viaja: solo se usa para calcular y comparar la firma localmente | Hasta rotación |
| **`STATUS_TOKEN`** (opcional) | **Confidencial** | Mismo fichero `.env` | Igual que el anterior | Cabecera `Authorization` sobre TLS del túnel | Hasta rotación |
| **Credencial del túnel** (`cd-receiver.json`) | **Confidencial** | `/etc/cloudflared/` | Permisos `0600`, fuera del repositorio | — | Hasta rotación |
| **Inventario de apps** (`apps.yml`) | **Interno** | `/etc/cd-receiver/apps.yml` | `0640` `root:deploy`; **excluido en `.gitignore`** | — | Vida del servicio |
| **Hostname del túnel** | **Interno** | Configuración de `cloudflared` y del webhook en GitHub | **No se versiona**: en los docs va como marcador de posición | — | — |
| **Payload del webhook** | **Público** | Memoria del proceso, no se persiste | No se escribe a disco | TLS hasta el Edge, túnel cifrado hasta el host | Efímero |
| **SHA del commit y tag de imagen** | **Público** | `/var/lib/cd-receiver/<app>.json` y logs | Texto plano | — | Vida del servicio |
| **Log de despliegues** (`.jsonl`) | **Interno** | `/var/log/cd-receiver/` | `0750` propiedad de `deploy`; puede contener salida de `docker compose` con nombres internos | — | Sin rotación automática (deuda, ver Gate 4) |
| **Credencial de GHCR en el host** | **N/A hoy** | — | El repositorio es público: el `pull` es anónimo y no hace falta `docker login` | — | — |

## Consecuencias de que el repositorio sea público

Se decidió publicarlo (charter). Eso fija tres reglas que el diseño ya cumple y que **cualquier
cambio futuro debe respetar**:

1. **La seguridad no puede depender del secreto del diseño.** Un atacante conoce las rutas, los
   encabezados que se validan y el algoritmo de firma. La única defensa real es el secreto HMAC
   — motivo por el que `config.load_settings` rechaza arrancar con menos de 32 caracteres.
2. **Nada específico de la instalación se versiona.** `.gitignore` excluye `config/apps.yml` y
   `.env`. El hostname del túnel aparece en la documentación como `deploy.<tu-dominio>` y no
   como el valor real: publicarlo solo añadiría superficie de escaneo sin ganar nada.
3. **Los ejemplos usan valores manifiestamente falsos.** `config/apps.example.yml` y el README
   emplean repositorios y dominios de ejemplo, nunca los reales.

## Controles derivados

| Control | Implementación | Verificación |
|---|---|---|
| El secreto nunca llega al repositorio | `.gitignore` + el instalador lo genera en el servidor | `git ls-files` no lista `.env` ni `apps.yml` |
| El secreto no puede ser débil | `ConfigError` si mide < 32 caracteres | `tests/test_config.py::test_falla_con_un_secreto_corto` |
| El payload no se persiste | Solo se registran `app`, `sha`, `tag` y resultado | Revisión de `deployer._append_log` |
| Los logs no salen del host | `LOG_DIR` local, sin envío externo | Inspección del servicio |
