# Porkbun DNS records for highergradetutoring.ca

Set these in **Domain Management → DNS** for `highergradetutoring.ca` at
<https://porkbun.com/account/domainsSpeedy>. Replace `YOUR.VM.IP` with your
Oracle Cloud VM's public IPv4 address.

## Required — points the domain at the VM

| Type | Host        | Answer        | TTL |
|------|-------------|---------------|-----|
| A    | (leave blank) | `YOUR.VM.IP` | 600 |
| A    | `www`       | `YOUR.VM.IP`  | 600 |

The blank-host `A` record covers `highergradetutoring.ca` (the apex/root).
The `www` record covers `www.highergradetutoring.ca`.

## Recommended — locks HTTPS issuance to Let's Encrypt

| Type | Host        | Answer                              | TTL |
|------|-------------|-------------------------------------|-----|
| CAA  | (leave blank) | `0 issue "letsencrypt.org"`       | 600 |

Prevents anyone else from getting a TLS cert for your domain even if they
somehow gained DNS control of a sub-record.

## Optional — pretty email forwarding

If you want `hello@highergradetutoring.ca` to forward to a Gmail address,
Porkbun has free email forwarding. Go to **Email → Forwarding** and add:

| Alias                              | Forward to              |
|------------------------------------|-------------------------|
| `hello@highergradetutoring.ca`     | `lucas.liu.ca2009@gmail.com` |
| `admin@highergradetutoring.ca`     | `lucas.liu.ca2009@gmail.com` |

Porkbun automatically adds the necessary MX records for you.

## Verifying it worked

After setting the A records, wait 5–15 minutes, then run from any computer:

```bash
dig +short highergradetutoring.ca
dig +short www.highergradetutoring.ca
```

Both should print your VM's public IP. If they're empty or wrong, give it
another 10 min — Porkbun's nameservers usually propagate quickly but TTLs
on caches elsewhere can lag.
