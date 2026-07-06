import { forwardRef } from 'react';
import { cn } from '@utils/helpers';
import styles from './Select.module.scss';

function renderOption(opt) {
  const value = typeof opt === 'object' ? opt.value : opt;
  const label = typeof opt === 'object' ? opt.label : opt;
  return (
    <option key={value} value={value} disabled={opt.disabled}>
      {label}
    </option>
  );
}

const Select = forwardRef(function Select(
  { label, id, error, hint, required, options = [], groups, placeholder, wrapperClassName, className, ...rest },
  ref,
) {
  const selectId = id || label?.toLowerCase().replace(/\s+/g, '-');

  return (
    <div className={cn(styles.field, wrapperClassName)}>
      {label && (
        <label htmlFor={selectId} className={styles.field__label}>
          {label}
          {required && <span className={styles.field__required} aria-hidden="true">*</span>}
        </label>
      )}
      <div className={styles.field__wrapper}>
        <select
          ref={ref}
          id={selectId}
          className={cn(styles.field__select, error && styles['field__select--error'], className)}
          aria-invalid={Boolean(error)}
          aria-describedby={error ? `${selectId}-error` : undefined}
          {...rest}
        >
          {placeholder && <option value="">{placeholder}</option>}
          {groups
            ? groups.map((group) => (
              <optgroup key={group.label} label={group.label}>
                {group.options.map(renderOption)}
              </optgroup>
            ))
            : options.map(renderOption)}
        </select>
        <span className={styles.field__chevron} aria-hidden="true">▾</span>
      </div>
      {hint && !error && <p className={styles.field__hint}>{hint}</p>}
      {error && <p id={`${selectId}-error`} className={styles.field__error} role="alert">{error}</p>}
    </div>
  );
});

export default Select;
