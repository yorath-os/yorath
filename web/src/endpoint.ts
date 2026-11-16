import { useEffect, useState } from 'react'

// The model endpoint as a client sees it: /v1 of this server, its root certificate, a curl call.

export const endpointUrl = () => `${window.location.origin}/v1`

export const CA_FILE = 'yorath-root-ca.crt'

// An agent key calls only agent/<name>; a key without an agent calls model-api models
const callModel = (agent: string | null) => (agent ? `agent/${agent}` : 'local/default')

export const curlExample = (key: string, ca: boolean, agent: string | null) =>
  `curl ${ca ? `--cacert ${CA_FILE} ` : ''}${endpointUrl()}/chat/completions \\\n` +
  `  -H "Authorization: Bearer ${key}" \\\n` +
  `  -H "Content-Type: application/json" \\\n` +
  `  -d '{"model": "${callModel(agent)}", "messages": [{"role": "user", "content": "Hello"}]}'`

// The root certificate is served only with the internal CA (Caddy, /yorath-root-ca.crt); otherwise 404
export const useOwnCa = () => {
  const [ca, setCa] = useState(false)
  useEffect(() => {
    const probe = async () => {
      try {
        const r = await fetch(`/${CA_FILE}`, { method: 'HEAD' })
        setCa(r.ok)
      } catch {
        setCa(false)
      }
    }
    void probe()
  }, [])
  return ca
}

// The certificate on the client machine: copied from the server, or downloaded and checked by its fingerprint
export const caCommands = () =>
  `scp root@${window.location.hostname}:/opt/yorath/installer/${CA_FILE} .\n` +
  `# or download it and compare its fingerprint with the one shown here:\n` +
  `curl -k -o ${CA_FILE} ${window.location.origin}/${CA_FILE}\n` +
  `openssl x509 -in ${CA_FILE} -noout -fingerprint -sha256`

// SHA-256 of the certificate as `openssl x509 -fingerprint -sha256` prints it: AA:BB:…
export const useCaFingerprint = (ca: boolean) => {
  const [fingerprint, setFingerprint] = useState<string | null>(null)
  useEffect(() => {
    if (!ca) return
    const read = async () => {
      try {
        const r = await fetch(`/${CA_FILE}`)
        if (!r.ok) return
        const b64 = (await r.text()).replace(/-----[A-Z ]+-----/g, '').replace(/\s+/g, '')
        const der = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0))
        const hash = new Uint8Array(await crypto.subtle.digest('SHA-256', der))
        setFingerprint(Array.from(hash, (b) => b.toString(16).padStart(2, '0').toUpperCase()).join(':'))
      } catch {
        setFingerprint(null)
      }
    }
    void read()
  }, [ca])
  return fingerprint
}
