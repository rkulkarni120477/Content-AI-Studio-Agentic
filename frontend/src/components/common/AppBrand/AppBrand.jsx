import { cn } from '@utils/helpers';
import styles from './AppBrand.module.scss';

const LOGO_SRC = '/ContentStudio.png';

/**
 * Brand block — matches Streamlit _sidebar_brand() using ContentStudio.png.
 */
export default function AppBrand({ variant = 'sidebar', showTag = true, className }) {
  const isHero = variant === 'hero';
  const logoHeight = isHero ? 52 : 32;

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
