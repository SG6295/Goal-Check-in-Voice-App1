"""Outbound SIP requests. Never log request headers or provider payloads."""
import httpx


def place_call(config, phone, variables):
    try:
        with httpx.Client(timeout=20) as client:
            response = client.post(
                "https://api.elevenlabs.io/v1/convai/sip-trunk/outbound-call",
                headers={"xi-api-key": config.elevenlabs_api_key},
                json={
                    "agent_id": config.elevenlabs_agent_id,
                    "agent_phone_number_id": config.elevenlabs_phone_number_id,
                    "to_number": phone,
                    "conversation_initiation_client_data": {"dynamic_variables": variables},
                },
            )
        try:
            result = response.json()
        except ValueError:
            result = {}
        if not isinstance(result, dict):
            result = {}
        if response.status_code == 200 and result.get("success") is True:
            return result, None
        message = str(result.get("message") or result.get("detail") or "Provider rejected the call")
        if config.elevenlabs_api_key:
            message = message.replace(config.elevenlabs_api_key, "[redacted]")
        return None, f"api_error: {response.status_code} {message[:300]}"
    except httpx.RequestError:
        return None, "api_error: network Request failed or timed out"
