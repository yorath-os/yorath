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
