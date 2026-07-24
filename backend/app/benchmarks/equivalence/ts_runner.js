/**
 * TypeScript SDK 调用桥
 *
 * 从 stdin 读取 JSON payload {operations, base_url, user_id, workspace_id}，
 * 用 TypeScript SDK 执行操作序列，将响应数组以 JSON 写到 stdout。
 *
 * 用法（由 Python 子进程调用）:
 *   echo '{"operations":[...],"base_url":"..."}' | node ts_runner.js
 */
const path = require('path');
const fs = require('fs');

// 尝试加载 TypeScript SDK
let MemoryClient;
try {
    // 优先尝试编译后的 dist
    const distPath = path.resolve(__dirname, '../../../sdk-typescript/dist/index.js');
    if (fs.existsSync(distPath)) {
        MemoryClient = require(distPath).MemoryClient;
    } else {
        // 降级到 src（需 ts-node，CI 环境可能不可用）
        MemoryClient = require('../../../sdk-typescript/src/index.ts').MemoryClient;
    }
} catch (e) {
    process.stderr.write(`加载 TypeScript SDK 失败: ${e.message}\n`);
    process.stdout.write('[]');
    process.exit(0);
}

async function executeOp(client, op) {
    const { op: name, args = {} } = op;
    try {
        let result;
        switch (name) {
            case 'remember':
                result = await client.remember(args.key, args.value, args.ttl);
                return { op: name, result, success: true };
            case 'recall':
                result = await client.recallContext(args.query, args.top_k || 5);
                return { op: name, result, success: true };
            case 'search':
                result = await client.search(args.query, args.top_k || 5, args.threshold || 0.3);
                return { op: name, result, success: true };
            case 'create_fragment':
                result = await client.rememberFragment(
                    args.content, args.fragment_type || 'fact',
                    args.importance_score || 0.5, args.ttl
                );
                return { op: name, result, success: true };
            case 'list_variables':
                result = await client.listVariables(args.session_id);
                return { op: name, result, success: true };
            case 'forget':
                result = await client.forget(args.key);
                return { op: name, result, success: true };
            case 'create_table':
                result = await client.createTable(args.table_name, args.fields);
                return { op: name, result, success: true };
            case 'list_tables':
                result = await client.listTables();
                return { op: name, result, success: true };
            case 'get_context':
                result = await client.getContext(args.session_id);
                return { op: name, result, success: true };
            default:
                return { op: name, error: `未知操作: ${name}`, success: false };
        }
    } catch (e) {
        return { op: name, error: e.message, success: false };
    }
}

async function main() {
    let input = '';
    process.stdin.setEncoding('utf8');
    for await (const chunk of process.stdin) {
        input += chunk;
    }

    if (!input.trim()) {
        process.stdout.write('[]');
        return;
    }

    let payload;
    try {
        payload = JSON.parse(input);
    } catch (e) {
        process.stderr.write(`JSON 解析失败: ${e.message}\n`);
        process.stdout.write('[]');
        return;
    }

    const { operations = [], base_url, user_id = 1, workspace_id } = payload;

    const client = new MemoryClient({
        baseUrl: base_url,
        userId: user_id,
        workspaceId: workspace_id ? String(workspace_id) : undefined,
    });

    const responses = [];
    try {
        for (const op of operations) {
            const resp = await executeOp(client, op);
            responses.push(resp);
        }
    } finally {
        if (client.close) await client.close();
    }

    process.stdout.write(JSON.stringify(responses));
}

main().catch(e => {
    process.stderr.write(`执行失败: ${e.message}\n`);
    process.stdout.write('[]');
});
