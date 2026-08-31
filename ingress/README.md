# Ingress

GitHub necesita alcanzar una URL HTTPS publica. El servidor no abre ni un
puerto: el tunel es quien establece la conexion, siempre de dentro hacia fuera.

## Dos formas de montar el tunel

Cloudflare admite dos modos, y **la documentacion de este repositorio describia solo el
primero mientras el despliegue real usaba el segundo**:

| Modo | Donde vive la configuracion | Cuando conviene |
|---|---|---|
| **Local** (`config.yml` + `credentials-file`) | En el servidor, versionable | Quieres la regla de ingress junto al codigo y bajo revision |
| **Remoto** (token del dashboard) | En Cloudflare Zero Trust | Gestionas varios tuneles desde un sitio; el contenedor solo lleva `--token` |

**El despliegue de `hgtech001` usa el modo remoto**, en un contenedor
`cloudflare/cloudflared` con `network_mode: host`. La regla de ingress se edita en el
dashboard, no en este repositorio: `cloudflared-config.yml` queda como referencia del modo
local y como especificacion de lo que la regla remota debe cumplir.

**En cualquiera de los dos modos, la regla obligatoria es la misma**: enrutar solo la ruta
`/webhook` y responder `404` al resto. Es el control de ADR-0004 y lo que cierra la amenaza
T13; el job `health.yml` lo verifica a diario contra el despliegue real.

Si usas el token, cuidado con exponerlo: aparece en `docker inspect`, en `ps` y en cualquier
volcado del comando del contenedor. Si se filtra, rotalo desde el dashboard y recrea el
contenedor.

## Cloudflare Tunnel — modo local

Ver `cloudflared-config.yml`. Puntos que suelen morder:

- **No pongas Cloudflare Access delante del hostname del webhook.** Access
  exige un login interactivo y GitHub recibiria un 302 a la pantalla de
  autenticacion. La autenticacion aqui es la firma HMAC, no una sesion.
- La regla `path: ^/webhook$` deja `/status` y `/reload` fuera de internet.
  Se consultan por SSH contra 127.0.0.1.
- Si quieres filtrar por IP de origen, hazlo en una WAF Custom Rule de
  Cloudflare con las redes que publica `https://api.github.com/meta` (campo
  `hooks`). En el servidor no sirve: alli toda peticion llega desde el tunel.

## Tailscale Funnel (alternativa)

Sin dominio propio, con hostname `*.ts.net`:

    tailscale funnel --bg --set-path /webhook http://127.0.0.1:9000/webhook

Comprueba con `tailscale funnel status` y usa esa URL en GitHub. Mas rapido de
montar; a cambio no tienes WAF ni reglas de borde.

## Comprobacion

Desde el servidor, contra el receptor local:

    curl -s http://127.0.0.1:9000/health

Desde fuera, contra la URL publica (debe dar 401, no 404: significa que la
peticion llego y fue rechazada por no venir firmada):

    curl -si https://deploy.tudominio.com/webhook -X POST -d '{}' | head -1
