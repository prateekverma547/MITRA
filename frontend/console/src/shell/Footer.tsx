import logoDark from "../assets/appinventiv-dark.png";
import logoLight from "../assets/appinventiv-light.png";

/**
 * On every page. The Appinventiv logo is recoloured to the theme (ink text on
 * light, near-white text on dark) with its background removed, so it sits in
 * the page rather than on a white box.
 */
export function Footer() {
  return (
    <footer className="site-footer">
      <span className="built-by">
        Built by
        <picture>
          <source srcSet={logoDark} media="(prefers-color-scheme: dark)" />
          <img src={logoLight} alt="Appinventiv" width={104} height={26} />
        </picture>
      </span>
      <span className="footer-note">MITRA gathers the evidence. A person makes every decision.</span>
    </footer>
  );
}
