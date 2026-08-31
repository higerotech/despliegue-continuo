# ADR-0004: Túnel de Cloudflare como único ingress, publicando solo `/webhook`

* **Estado:** accepted
* **Fecha:** 2026-08-30
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 02-design
* **Versión:** 1.1.0
* **ID:** ADR-0004
* **Supersede / Superseded-by:** —
* **Controles OWASP afectados:** A01 (control de acceso), A04 (fallos criptográficos / TLS), A02 (configuración de seguridad)

## Contexto

Origina: RNF01 y RS01. GitHub necesita alcanzar una URL HTTPS pública, y el servidor está
detrás de NAT sin IP pública utilizable. El operador ya explota túneles de Cloudflare en otros
proyectos (`signoz-deployment`, ADR-0003 de ese repositorio), y existe una cuenta con dominio
gestionado.

El receptor expone cuatro rutas con sensibilidades distintas: `/webhook` debe ser alcanzable
por GitHub; `/health` es inocuo; `/status` revela el inventario y el historial de despliegues;
`/reload` cambia la configuración en caliente.

## Decisión

`cloudflared` en el host es el **único punto de entrada**. Conexión saliente y persistente
hacia Cloudflare Edge; ningún puerto del receptor se publica ni se enlaza a interfaces
públicas (`BIND_HOST=127.0.0.1`, comprobado por
`tests/test_config.py::test_escucha_en_loopback_por_defecto`).

La regla de ingress **filtra por ruta**: solo `^/webhook$` llega al receptor; todo lo demás
recibe `404` en el borde. `/status` y `/reload` quedan fuera de Internet por construcción, y
además exigen origen loopback o `STATUS_TOKEN` en el propio receptor — dos capas independientes.

**No se pone Cloudflare Access delante de este hostname.** Access exige un login interactivo y
GitHub recibiría un `302` a la pantalla de autenticación en lugar de entregar el webhook. La
autenticación aquí es la firma HMAC, no una sesión de usuario. Es la diferencia con
`signoz-deployment`, donde la UI sí va tras Access porque la consumen personas.

El hostname real **no se versiona**: la documentación usa `deploy.<tu-dominio>` porque este
repositorio es público (ver `data-classification.md`).

## Alternativas consideradas

| Opción | Pros | Contras | Riesgo de seguridad |
|---|---|---|---|
| **Túnel Cloudflare + filtro por ruta (elegida)** | Cero puertos abiertos; TLS gestionado; WAF disponible; filtrado en el borde | Dependencia de Cloudflare | Robo del token del túnel (T8) |
| Tailscale Funnel | Igual de simple; sin dominio propio | Sin WAF ni reglas de borde; hostname `*.ts.net` | Similar, con menos control |
| Port forwarding + Caddy/nginx + Let's Encrypt | Sin dependencia externa | Puerto 443 abierto y escaneado; renovación de certificados; DDNS | Superficie directa a Internet |
| VPN (WireGuard) hacia GitHub | Perímetro fuerte | **Inviable**: GitHub no se conecta por VPN | — |

## Consecuencias

- Positivas: RNF01 se cumple por construcción — `ss -ltnp` en el host no muestra ningún puerto
  escuchando en `0.0.0.0`. La IP del servidor no revela servicios. TLS es responsabilidad de
  Cloudflare, sin certificados que renovar.
- Negativas / deuda asumida: la disponibilidad del despliegue queda atada a Cloudflare. La
  degradación es benigna: si el túnel cae, los webhooks fallan y GitHub los marca como no
  entregados; nada se rompe y el operador puede desplegar a mano con `IMAGE_TAG`. GitHub
  reintenta, pero **no indefinidamente**: una caída larga exige reenviar la entrega desde la UI.
- Impacto en threat model: mitiga **T3** (escaneo y ataque directo a puertos) y **T7**
  (exposición de `/status` y `/reload`). Introduce **T8** (robo de la credencial del túnel),
  mitigada con permisos `0600` fuera del repositorio y rotación.
- Efecto lateral relevante: al llegar todo el tráfico desde el túnel, **una allowlist de IPs de
  GitHub en el host no sirve de nada** — el origen siempre es Cloudflare. Si se quisiera ese
  filtro, va en una regla WAF del borde con las redes de `api.github.com/meta` (campo `hooks`).

## Estado real del despliegue (2026-08-31)

El túnel montado en `hgtech001` **no aplica el filtro por ruta** que esta ADR especifica:
publica `deploy.higerotech.com` contra `http://localhost:9000` completo. Se gestiona por token
desde el dashboard de Cloudflare, no con el `config.yml` de `ingress/cloudflared-config.yml`.

La decisión de esta ADR **no cambia**: el filtro por ruta sigue siendo lo correcto y es la
corrección pendiente. Lo que cambia es que el estado real no la cumple, y eso está registrado
como deuda **DS-07** con su amenaza asociada **T13** en
[`threat-model.md`](../../02-design/threat-model.md).

Comprobado que hoy `/status` y `/reload` responden `403` desde Internet, pero por una cadena de
comportamientos que no diseñamos —el reescrito de `request.client` que hace uvicorn con
`--proxy-headers` y la normalización de `X-Forwarded-For` que hace Cloudflare—. Esa dependencia
es precisamente lo que el filtro por ruta elimina.
