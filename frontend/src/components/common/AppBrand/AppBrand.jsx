import { cn } from '@utils/helpers';
import styles from './AppBrand.module.scss';

const LOGO_SRC = '/ContentStudio.png';

/**
 * Brand block — matches Streamlit _sidebar_brand() using ContentStudio.png.
 * `compact` renders the logo alone (collapsed-sidebar rail).
 */
export default function AppBrand({ variant = 'sidebar', showTag = true, compact = false, className }) {
  const isHero = variant === 'hero';
  const logoHeight = isHero ? 52 : 32;

  if (compact) {
    return (
      <div className={cn(styles.brand, styles['brand--compact'], className)}>
        <img
          src={LOGO_SRC}
          alt="Content AI Studio"
          className={styles.logo}
          height={logoHeight}
          title="Content AI Studio"
        />
      </div>
    );
  }

  return (
    <div className={cn(styles.brand, isHero && styles['brand--hero'], className)}>
      <div className={styles.titleRow}>
        <img
          src={LOGO_SRC}
          alt=""
          className={styles.logo}
          height={logoHeight}
          aria-hidden="true"
        />
        <span className={styles.title}>Content AI Studio</span>
      </div>
      {showTag && <div className={styles.tag}>Enterprise AI Platform</div>}
    </div>
  );
}
