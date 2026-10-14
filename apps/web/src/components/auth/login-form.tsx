"use client";

import { CircleAlert, ScanEye } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { useI18n } from "@/i18n/provider";
import { describeError } from "@/lib/api/client";
import { CSRF_HEADERS } from "@/lib/auth";

/** Only paths on this site, so a crafted link cannot send the user elsewhere after sign-in. */
function safeNext(value: string | null): string {
  return value && value.startsWith("/") && !value.startsWith("//") && !value.startsWith("/login")
    ? value
    : "/";
}

export function LoginForm() {
  const { t, f } = useI18n();
  const m = t.auth;
  const params = useSearchParams();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const submit = async () => {
    setPending(true);
    setError(null);
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { ...CSRF_HEADERS, "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ username, password }),
      });
      if (response.ok) {
        // A full load, so the app starts clean with the new session.
        window.location.replace(safeNext(params.get("next")));
        return;
      }
      const body: unknown = await response.json().catch(() => null);
      setError(f.server(describeError(body, m.failed)));
    } catch {
      setError(t.errors.unreachable);
    }
    setPassword("");
    setPending(false);
  };

  return (
    <Card className="w-full max-w-sm">
      <CardHeader className="items-center text-center">
        <div className="bg-primary text-primary-foreground mb-2 flex size-10 items-center justify-center rounded-lg">
          <ScanEye className="size-5" aria-hidden />
        </div>
        <CardTitle className="text-xl">{m.title}</CardTitle>
        <CardDescription>{m.description}</CardDescription>
      </CardHeader>
      <CardContent>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <FieldGroup>
            {error ? (
              <Alert variant="destructive">
                <CircleAlert aria-hidden />
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            ) : null}
            <Field>
              <FieldLabel htmlFor="login-username">{m.username}</FieldLabel>
              <Input
                id="login-username"
                autoComplete="username"
                autoCapitalize="none"
                spellCheck={false}
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                required
                autoFocus
              />
            </Field>
            <Field>
              <FieldLabel htmlFor="login-password">{m.password}</FieldLabel>
              <Input
                id="login-password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                required
              />
            </Field>
            <Button type="submit" className="w-full" disabled={pending || !username || !password}>
              {pending ? <Spinner /> : null}
              {pending ? m.signingIn : m.signIn}
            </Button>
          </FieldGroup>
        </form>
      </CardContent>
    </Card>
  );
}
