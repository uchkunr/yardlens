// Regenerates src/lib/api/schema.d.ts from the running API (`bun run gen:api`).
// A script rather than `${API_URL:-…}` in package.json, because Bun's shell on Windows has no
// default-value expansion. Bun loads .env.local itself, so NEXT_PUBLIC_API_URL applies here too.
import { $ } from "bun";

const apiUrl = (
  process.env.API_URL ??
  process.env.NEXT_PUBLIC_API_URL ??
  "http://localhost:8100"
).replace(/\/$/, "");
const schemaUrl = `${apiUrl}/openapi.json`;

await $`openapi-typescript ${schemaUrl} -o src/lib/api/schema.d.ts`;
