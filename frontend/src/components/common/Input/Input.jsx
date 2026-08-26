import { forwardRef, useState } from 'react';
import { cn } from '@utils/helpers';
import EyeIcon from '@components/common/EyeIcon/EyeIcon';
import styles from './Input.module.scss';

const Input = forwardRef(function Input(
  {
    label,
    id,
    error,
    hint,
    required,
    className,
    wrapperClassName,
    type = 'text',
    ...rest
  },
  ref,
) {
  const [passwordVisible, setPasswordVisible] = useState(false);
  const inputId = id || label?.toLowerCase().replace(/\s+/g, '-');
  const isPassword = type === 'password';
  const inputType = isPassword && passwordVisible ? 'text' : type;

  return (
    <div className={cn(styles.field, wrapperClassName)}>
      {label && (
        <label htmlFor={inputId} className={styles.field__label}>
          {label}
          {required && <span className={styles.field__required} aria-hidden="true">*</span>}
        </label>
      )}
      <div className={isPassword ? styles.field__control : undefined}>
        <input
          ref={ref}
          id={inputId}
          type={inputType}
          className={cn(
            styles.field__input,
            isPassword && styles['field__input--withToggle'],
            error && styles['field__input--error'],
            className,
          )}
          aria-invalid={Boolean(error)}
          aria-describedby={error ? `${inputId}-error` : hint ? `${inputId}-hint` : undefined}
          {...rest}
        />
        {isPassword && (
          <button
            type="button"
            className={styles.field__toggle}
            onClick={() => setPasswordVisible((v) => !v)}
            aria-label={passwordVisible ? 'Hide password' : 'Show password'}
            aria-pressed={passwordVisible}
          >
            <EyeIcon hidden={passwordVisible} />
          </button>
        )}
      </div>
      {hint && !error && (
        <p id={`${inputId}-hint`} className={styles.field__hint}>{hint}</p>
      )}
      {error && (
        <p id={`${inputId}-error`} className={styles.field__error} role="alert">{error}</p>
      )}
    </div>
  );
});

export default Input;
