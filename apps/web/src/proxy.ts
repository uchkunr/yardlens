import { NextResponse, type NextRequest } from "next/server";

/**
 * Sends visitors without a session to the sign-in page before the app shell renders. The
 * cookie checked here only says that a sign-in exists; the API decides whether it is still valid,
 * and the app goes back to /login if it is not.
 */
export function proxy(request: NextRequest) {
  const signedIn = request.cookies.has("yl_session");
  const { pathname, search } = request.nextUrl;
  if (pathname === "/login") {
    return signedIn ? NextResponse.redirect(new URL("/", request.url)) : NextResponse.next();
  }
  if (signedIn) return NextResponse.next();
  const login = new URL("/login", request.url);
  if (pathname !== "/") login.searchParams.set("next", `${pathname}${search}`);
  return NextResponse.redirect(login);
}

export const config = {
  // API and media requests are rewritten to the API, which checks them itself.
  matcher: ["/((?!api|media|_next/static|_next/image|icon.svg|favicon.ico).*)"],
};
