import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Link, type LinkProps } from "react-router-dom";
import { Spinner } from "./Spinner";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "md" | "sm";

function classes(variant: Variant, size: Size, extra?: string) {
  return ["btn", `btn-${variant}`, size === "sm" ? "btn-sm" : "", extra ?? ""].filter(Boolean).join(" ");
}

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  icon?: ReactNode;
  /** Shows a spinner in place of the icon and disables the button. */
  loading?: boolean;
}

/** Every action is a button. Links that look like buttons are ButtonLink. */
export function Button({ variant = "secondary", size = "md", icon, loading, children, className, disabled, ...rest }: ButtonProps) {
  return (
    <button
      type="button"
      {...rest}
      className={classes(variant, size, className)}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
    >
      {loading ? <Spinner size={16} /> : icon}
      {children && <span>{children}</span>}
    </button>
  );
}

interface ButtonLinkProps extends LinkProps {
  variant?: Variant;
  size?: Size;
  icon?: ReactNode;
}

/** Navigation that looks and feels like a button, with the page transition. */
export function ButtonLink({ variant = "secondary", size = "md", icon, children, className, ...rest }: ButtonLinkProps) {
  return (
    <Link viewTransition {...rest} className={classes(variant, size, className)}>
      {icon}
      {children && <span>{children}</span>}
    </Link>
  );
}
