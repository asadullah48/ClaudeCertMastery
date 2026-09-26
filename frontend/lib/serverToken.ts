import { auth } from "@clerk/nextjs/server";

/** The signed-in learner's Clerk session token for a server-component fetch, or null
 * when signed out. Browser-side calls resolve their own token inside lib/api.ts. */
export async function serverToken(): Promise<string | null> {
  const { getToken } = await auth();
  return getToken();
}
