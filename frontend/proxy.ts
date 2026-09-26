import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";

// Learner-private pages: evidence is written or read here, so a signed-out visitor is
// sent to sign-in and returned afterwards. The landing page, track catalog and
// methodology stay public. This is a UX gate only -- the backend independently
// rejects any learner-private request without a valid session.
const isLearnerRoute = createRouteMatcher([
  "/tracks/:code/exam(.*)",
  "/tracks/:code/scenarios(.*)",
  "/tracks/:code/readiness(.*)",
]);

export default clerkMiddleware(async (auth, req) => {
  if (isLearnerRoute(req)) await auth.protect();
});

export const config = {
  matcher: [
    // Skip Next.js internals and all static files, unless found in search params
    "/((?!_next|[^?]*\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    "/(api|trpc)(.*)",
    "/__clerk/(.*)",
  ],
};
