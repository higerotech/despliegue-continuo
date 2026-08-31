# Ingress

GitHub necesita alcanzar una URL HTTPS publica. El servidor no abre ni un
puerto: el tunel es quien establece la conexion, siempre de dentro hacia fuera.

## Cloudflare Tunnel (recomendado)

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
