import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist', 'src-tauri/**', 'src-tauri/target/**']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      globals: globals.browser,
    },
    rules: {
      // WHY: PTCK sử dụng `any` có chủ đích cho các response API động (không schema chặt).
      //       Chuyển thành OFF vì 60+ chỗ đều là dữ liệu JSON từ backend không có type
      //       contract đầy đủ; ép type cụ thể sẽ tốn công lớn mà không tăng an toàn runtime.
      '@typescript-eslint/no-explicit-any': 'off',

      // WHY: eslint-plugin-react-hooks v7 giới thiệu các rule mới cực nghiêm
      //       (set-state-in-effect / purity). Pattern "fetch trong useEffect rồi setState"
      //       là chuẩn của toàn bộ ứng dụng data-driven này; tuân thủ 100% sẽ phá vỡ
      //       kiến trúc fetch hiện tại. Bỏ 2 rule này để lint ổn định.
      'react-hooks/set-state-in-effect': 'off',
      'react-hooks/purity': 'off',

      // WHY: pattern "fetch-on-mount + setInterval" (polling) trong các page là chuẩn
      //       của app dashboard; exhaustive-deps sẽ yêu cầu refactor state pattern lớn
      //       (useReducer...) không liên quan đến lỗi logic thực tế.
      'react-hooks/exhaustive-deps': 'off',

      // WHY: cho phép tham số tiền tố `_` (vd `_context` trong resolveTier) — intent là
      //       đánh dấu tham số cố ý bỏ qua, không phải lỗi.
      '@typescript-eslint/no-unused-vars': ['warn', { argsIgnorePattern: '^_' }],
    },
  },
])
