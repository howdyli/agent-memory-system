import { test, expect } from '@playwright/test';

test.describe('登录流程', () => {
  test.beforeEach(async ({ page }) => {
    // Mock auth/me to return 401 (not logged in) by default
    await page.route('**/api/v1/auth/me', async (route) => {
      await route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Not authenticated' }),
      });
    });
  });

  test('输入有效凭据登录成功后跳转到 dashboard 并显示用户名', async ({ page }) => {
    // Mock login API success
    await page.route('**/api/v1/auth/login', async (route) => {
      const body = JSON.parse(route.request().postData() || '{}');
      expect(body.username).toBe('testuser');
      expect(body.password).toBe('password123');
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          access_token: 'fake-jwt-token',
          token_type: 'bearer',
          user_id: 1,
          username: 'testuser',
        }),
      });
    });

    // Mock auth/me for after login
    await page.route('**/api/v1/auth/me', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          user_id: 1,
          username: 'testuser',
          email: 'test@example.com',
        }),
      });
    });

    // Navigate to login page
    await page.goto('/login');

    // Fill in credentials
    await page.getByPlaceholder('用户名').fill('testuser');
    await page.getByPlaceholder('密码').fill('password123');

    // Click login button
    await page.getByRole('button', { name: '登录' }).click();

    // Verify navigation to dashboard (root route)
    await expect(page).toHaveURL('/');

    // Mock dashboard API calls to prevent errors
    await page.route('**/api/v1/**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({}),
      });
    });

    // Verify username is displayed in the header
    await expect(page.getByText('testuser')).toBeVisible();
  });

  test('输入无效凭据显示错误信息并保持在登录页', async ({ page }) => {
    // Mock login API failure
    await page.route('**/api/v1/auth/login', async (route) => {
      await route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Invalid credentials' }),
      });
    });

    await page.goto('/login');

    // Fill in invalid credentials
    await page.getByPlaceholder('用户名').fill('wronguser');
    await page.getByPlaceholder('密码').fill('wrongpass');

    // Click login button
    await page.getByRole('button', { name: '登录' }).click();

    // Verify error message is shown
    await expect(page.getByText('Invalid credentials')).toBeVisible();

    // Verify still on login page
    await expect(page).toHaveURL('/login');
  });

  test('点击登出后跳回登录页', async ({ page }) => {
    // Set up authenticated state
    await page.addInitScript(() => {
      localStorage.setItem('access_token', 'fake-jwt-token');
      localStorage.setItem('user', JSON.stringify({ id: 1, username: 'testuser' }));
    });

    // Mock auth/me for authenticated state
    await page.route('**/api/v1/auth/me', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          user_id: 1,
          username: 'testuser',
          email: 'test@example.com',
        }),
      });
    });

    // Mock all other API calls
    await page.route('**/api/v1/**', async (route) => {
      if (route.request().url().includes('/auth/me')) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ user_id: 1, username: 'testuser', email: 'test@example.com' }),
        });
      } else {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({}),
        });
      }
    });

    // Navigate to dashboard
    await page.goto('/');
    await expect(page.getByText('testuser')).toBeVisible();

    // Click on user avatar/dropdown to open menu
    await page.locator('.app-header').getByText('testuser').click();

    // Click logout
    await page.getByText('退出登录').click();

    // Verify redirect to login page
    await expect(page).toHaveURL('/login');
  });
});
