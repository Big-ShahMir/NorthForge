import { Link } from "react-router";

export function NotFoundPage() {
  return (
    <div className="max-w-2xl space-y-2">
      <h1 className="text-2xl font-semibold tracking-tight">Page not found</h1>
      <p className="text-muted-foreground text-sm">
        Nothing lives at this address.{" "}
        <Link to="/settings" className="underline">
          Go to Settings
        </Link>
        .
      </p>
    </div>
  );
}
