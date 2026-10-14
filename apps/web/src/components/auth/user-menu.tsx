"use client";

import { KeyRound, LogOut, MonitorX, UserRound } from "lucide-react";
import { useState } from "react";

import { useSession } from "@/components/auth/session-provider";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { useI18n } from "@/i18n/provider";
import { useChangePassword } from "@/lib/api/queries";

function ChangePasswordDialog({ onOpenChange }: { onOpenChange: (open: boolean) => void }) {
  const { t } = useI18n();
  const m = t.auth;
  const change = useChangePassword();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const mismatch = repeat.length > 0 && repeat !== next;

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (mismatch) return;
            change.mutate(
              { current_password: current, new_password: next },
              { onSuccess: () => onOpenChange(false) },
            );
          }}
        >
          <DialogHeader>
            <DialogTitle>{m.changePassword}</DialogTitle>
            <DialogDescription>{m.passwordHint}</DialogDescription>
          </DialogHeader>
          <FieldGroup className="py-4">
            <Field>
              <FieldLabel htmlFor="current-password">{m.currentPassword}</FieldLabel>
              <Input
                id="current-password"
                type="password"
                autoComplete="current-password"
                value={current}
                onChange={(event) => setCurrent(event.target.value)}
                required
              />
            </Field>
            <Field>
              <FieldLabel htmlFor="new-password">{m.newPassword}</FieldLabel>
              <Input
                id="new-password"
                type="password"
                autoComplete="new-password"
                minLength={10}
                value={next}
                onChange={(event) => setNext(event.target.value)}
                required
              />
              <FieldDescription>{m.passwordRule}</FieldDescription>
            </Field>
            <Field data-invalid={mismatch}>
              <FieldLabel htmlFor="repeat-password">{m.repeatPassword}</FieldLabel>
              <Input
                id="repeat-password"
                type="password"
                autoComplete="new-password"
                value={repeat}
                onChange={(event) => setRepeat(event.target.value)}
                aria-invalid={mismatch}
                required
              />
              {mismatch ? <FieldError errors={[{ message: m.passwordsDiffer }]} /> : null}
            </Field>
          </FieldGroup>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              {t.common.cancel}
            </Button>
            <Button type="submit" disabled={change.isPending || mismatch || !current || !next}>
              {change.isPending ? <Spinner /> : null}
              {m.changePassword}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function UserMenu() {
  const { user, signOut, signOutEverywhere } = useSession();
  const { t } = useI18n();
  const m = t.auth;
  const [changing, setChanging] = useState(false);

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="sm" aria-label={m.account}>
            <UserRound aria-hidden />
            <span className="hidden max-w-32 truncate sm:inline">{user?.username ?? ""}</span>
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-56">
          <DropdownMenuLabel className="truncate font-normal">{user?.username ?? m.account}</DropdownMenuLabel>
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => setChanging(true)}>
            <KeyRound aria-hidden /> {m.changePassword}
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => void signOut()}>
            <LogOut aria-hidden /> {m.signOut}
          </DropdownMenuItem>
          <DropdownMenuItem variant="destructive" onSelect={() => void signOutEverywhere()}>
            <MonitorX aria-hidden /> {m.signOutEverywhere}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      {changing ? <ChangePasswordDialog onOpenChange={setChanging} /> : null}
    </>
  );
}
