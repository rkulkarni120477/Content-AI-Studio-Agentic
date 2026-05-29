import { forwardRef } from 'react';
import { cn } from '@utils/helpers';
import Loader from '../Loader/Loader';
import styles from './Button.module.scss';

const Button = forwardRef(function Button(
  {
    variant  = 'primary',
    size     = 'md',
    loading  = false,
    disabled = false,
    icon,
    iconPosition = 'left',
    fullWidth = false,
    type     = 'button',
    className,
    children,
    ...rest
  },
  ref,
) {
  const isDisabled = disabled || loading;

  return (
    <button
      ref={ref}
      type={type}
      disabled={isDisabled}
      className={cn(
        styles.btn,
        styles[`btn--${variant}`],
        styles[`btn--${size}`],
        fullWidth && styles['btn--full'],
        loading   && styles['btn--loading'],
        className,
      )}
      aria-busy={loading}
      {...rest}
    >
      {loading && (
        <span className={styles.btn__spinner} aria-hidden="true">
          <Loader size="sm" color="inherit" />
        </span>
      )}
      {!loading && icon && iconPosition === 'left' && (
        <span className={styles.btn__icon} aria-hidden="true">{icon}</span>
      )}
      {children && <span className={styles.btn__label}>{children}</span>}
      {!loading && icon && iconPosition === 'right' && (
        <span className={styles.btn__icon} aria-hidden="true">{icon}</span>
      )}
    </button>
  );
});

export default Button;
