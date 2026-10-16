from app.main import app

schema = app.openapi()
out = [
    "# API Contract",
    "",
    "Generated from the OpenAPI schema. Interactive docs are served at `/docs`.",
    "All routes except auth and webhooks require `Authorization: Bearer <access_token>`.",
    "",
]

for path, methods in sorted(schema["paths"].items()):
    for method, op in methods.items():
        out.append(f"## {method.upper()} `{path}`")
        if op.get("summary"):
            out.append(op["summary"])
        params = [p["name"] + (" (required)" if p.get("required") else "") for p in op.get("parameters", [])]
        if params:
            out.append("")
            out.append("**Parameters:** " + ", ".join(params))
        body = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema", {})
        if body.get("$ref"):
            out.append("")
            out.append("**Request body:** " + body["$ref"].split("/")[-1])
        out.append("")
        out.append("**Responses:** " + ", ".join(op.get("responses", {}).keys()))
        out.append("")

out.append("## WebSocket")
out.append("")
out.append("Endpoint: `/api/v1/ws`. Send `{\"type\": \"auth\", \"token\": \"<access_token>\"}` first; the server replies `{\"type\": \"auth.ok\"}`.")
out.append("")
out.append("Events (envelope: `type`, `data`, `timestamp`): `message.new`, `conversation.updated`, `conversation.needs_human`.")
out.append("")

with open("API_CONTRACT.md", "w", encoding="utf-8") as f:
    f.write("\n".join(out))
print("Wrote API_CONTRACT.md")