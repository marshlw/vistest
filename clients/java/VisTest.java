// VisTest - self-hosted visual regression testing.
// Copyright (C) 2026 Kirill Kulagin
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// This file is part of VisTest. See LICENSE for the full terms and NOTICE for
// the trademark and commercial-licensing terms. Removing this header does not
// remove those obligations.

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;

/**
 * VisTest client for the JVM. One file, no dependencies, Java 11+.
 *
 * <p>Why one file and not a Maven artifact. A Java agent that replaces the
 * comparison inside somebody else's suite is the most expensive thing on the
 * roadmap and the least verifiable: every suite hooks its screenshots
 * differently, and an agent that guesses wrong fails silently. A dependency in
 * their {@code pom.xml} is also not free — it is a review, a version, a
 * security sign-off. A file you can read in five minutes and drop into
 * {@code src/test/java} is the amount of change a team actually accepts.
 *
 * <p>For a JVM suite this is the SECOND choice, and that is deliberate. The
 * first is to let the suite run where it already runs and hand VisTest the
 * folder afterwards — {@code vistest project ingest <key> --dir target/screenshots}.
 * That needs no code in their repository at all. Use this client when the
 * verdict has to be available inside the test, at the moment of the check.
 *
 * <pre>{@code
 * VisTest vt = new VisTest(System.getenv("VISTEST_API_URL"))
 *         .project("web-e2e")
 *         .token(System.getenv("VISTEST_TOKEN"))
 *         .runKey(System.getenv("CI_JOB_ID"));
 *
 * VisTest.Result r = vt.check("checkout.png", screenshotBytes);
 * assertTrue(r.message(), r.passed);
 * ...
 * vt.finish();   // one run in the history, not a scatter of checks
 * }</pre>
 *
 * <p>Also runnable straight from a shell, without compiling anything:
 * <pre>{@code
 * java VisTest.java http://localhost:8420 web-e2e checkout.png shot.png
 * }</pre>
 */
public final class VisTest {

    private final String apiUrl;
    private final HttpClient http = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(15))
            .build();

    private String project = "default";
    private String browser = "chromium";
    private String token = "";
    private String runKey = "";
    private final List<String> bodies = new ArrayList<>();
    private int failed = 0;
    private int created = 0;
    private String platform = "";

    public VisTest(String apiUrl) {
        if (apiUrl == null || apiUrl.isBlank()) {
            apiUrl = "http://127.0.0.1:8420";
        }
        this.apiUrl = apiUrl.endsWith("/")
                ? apiUrl.substring(0, apiUrl.length() - 1) : apiUrl;
    }

    public VisTest project(String value) { this.project = nz(value, project); return this; }
    public VisTest browser(String value) { this.browser = nz(value, browser); return this; }
    public VisTest token(String value) { this.token = nz(value, ""); return this; }
    public VisTest runKey(String value) { this.runKey = nz(value, ""); return this; }

    /** The verdict of one snapshot. */
    public static final class Result {
        public final String name;
        public final String verdict;   // pass | fail | new_baseline | error
        public final boolean passed;
        public final String raw;       // the whole response, for the message

        Result(String name, String verdict, boolean passed, String raw) {
            this.name = name;
            this.verdict = verdict;
            this.passed = passed;
            this.raw = raw;
        }

        /** Something a failing assertion can print without hiding the cause. */
        public String message() {
            return "VisTest: " + name + " -> " + verdict
                    + (passed ? "" : "\n" + raw);
        }
    }

    /** Compare a PNG against the baseline VisTest holds for this name. */
    public Result check(String name, byte[] png) throws IOException, InterruptedException {
        Multipart form = new Multipart()
                .field("name", name)
                .field("project", project)
                .field("browser", browser);
        if (!runKey.isEmpty()) {
            form.field("run_key", runKey);
        }
        form.file("image", "actual.png", "image/png", png);

        HttpRequest.Builder request = HttpRequest.newBuilder()
                .uri(URI.create(apiUrl + "/api/check"))
                .timeout(Duration.ofSeconds(120))
                .header("Content-Type", form.contentType())
                .POST(HttpRequest.BodyPublishers.ofByteArray(form.body()));
        if (!token.isEmpty()) {
            request.header("X-VisTest-Token", token);
        }

        HttpResponse<String> response =
                http.send(request.build(), HttpResponse.BodyHandlers.ofString());
        if (response.statusCode() >= 300) {
            throw new IOException("VisTest API " + response.statusCode() + ": "
                    + response.body());
        }

        String raw = response.body();
        bodies.add(raw);
        if (platform.isEmpty()) {
            platform = string(raw, "platform");
        }
        String verdict = string(raw, "verdict");
        if ("fail".equals(verdict)) {
            failed++;
        } else if ("new_baseline".equals(verdict)) {
            created++;
        }
        return new Result(string(raw, "name"), verdict, !"fail".equals(verdict), raw);
    }

    public Result check(String name, Path png) throws IOException, InterruptedException {
        return check(name, Files.readAllBytes(png));
    }

    /**
     * Close the run: turn the checks into one entry in the history.
     *
     * <p>Without it every check lives on its own — a verdict comes back,
     * pictures land on disk, and nothing appears in the run list. No history
     * for the snapshot, no review queue, no answer to "this build broke three
     * screens". Only the suite knows when the checks are over, so only the
     * suite can say so. Needs {@code runKey}: that is what names the run.
     */
    public void finish() throws IOException, InterruptedException {
        if (runKey.isEmpty()) {
            throw new IllegalStateException("finish() needs a runKey: it names the run");
        }
        if (bodies.isEmpty()) {
            return;
        }

        int total = bodies.size();
        StringBuilder json = new StringBuilder(256 + total * 512);
        json.append("{\"run_id\":\"").append(escape(runKey))
            .append("\",\"project\":\"").append(escape(project))
            .append("\",\"browser\":\"").append(escape(browser))
            .append("\",\"platform\":\"").append(escape(platform))
            .append("\",\"git\":{\"branch\":\"").append(escape(env("VISTEST_BRANCH",
                    env("CI_COMMIT_REF_NAME", env("GITHUB_REF_NAME", "")))))
            .append("\",\"sha\":\"").append(escape(env("VISTEST_COMMIT",
                    env("CI_COMMIT_SHA", env("GITHUB_SHA", "")))))
            .append("\"},\"totals\":{\"total\":").append(total)
            .append(",\"failed\":").append(failed)
            .append(",\"new\":").append(created)
            .append(",\"passed\":").append(total - failed - created)
            .append("},\"comparisons\":[");
        // Ответы сервиса вставляются как есть. Собирать их заново значило бы
        // писать JSON-сериализатор ради данных, которые уже пришли валидным
        // JSON'ом — и терять на этом регионы, метрики и ссылки на артефакты.
        for (int i = 0; i < total; i++) {
            if (i > 0) {
                json.append(',');
            }
            json.append(bodies.get(i));
        }
        json.append("]}");

        HttpRequest.Builder request = HttpRequest.newBuilder()
                .uri(URI.create(apiUrl + "/api/runs"))
                .timeout(Duration.ofSeconds(60))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(json.toString(),
                        StandardCharsets.UTF_8));
        if (!token.isEmpty()) {
            request.header("X-VisTest-Token", token);
        }

        HttpResponse<String> response =
                http.send(request.build(), HttpResponse.BodyHandlers.ofString());
        if (response.statusCode() >= 300) {
            throw new IOException("VisTest API " + response.statusCode() + ": "
                    + response.body());
        }
    }

    public int failedCount() { return failed; }
    public int totalCount() { return bodies.size(); }

    // ---------------------------------------------------------------- //
    //  Разбор ответа
    //
    //  Достаются ровно три строковых поля верхнего уровня, и на этом всё.
    //  Тащить JSON-библиотеку в чужой pom.xml ради `verdict` — это ревью,
    //  версия и согласование безопасности вместо одной строки; полный ответ
    //  всё равно уезжает в сообщение об ошибке, где он и нужен целиком.
    // ---------------------------------------------------------------- //
    static String string(String json, String key) {
        String needle = "\"" + key + "\"";
        int at = json.indexOf(needle);
        if (at < 0) {
            return "";
        }
        // Пробелы между ключом, двоеточием и значением допустимы по формату, и
        // тот, кто отдаёт ответ, волен их ставить: прокси, другой сериализатор,
        // «красивый» JSON в логе. Привязка к `"key":"` работала ровно с одним
        // из этих вариантов и молча возвращала пустую строку на остальных —
        // то есть вердикт становился пустым, а снимок «прошедшим».
        int i = at + needle.length();
        while (i < json.length() && Character.isWhitespace(json.charAt(i))) {
            i++;
        }
        if (i >= json.length() || json.charAt(i) != ':') {
            return "";
        }
        i++;
        while (i < json.length() && Character.isWhitespace(json.charAt(i))) {
            i++;
        }
        if (i >= json.length() || json.charAt(i) != '"') {
            return "";
        }
        StringBuilder out = new StringBuilder();
        for (int at2 = i + 1; at2 < json.length(); at2++) {
            char c = json.charAt(at2);
            if (c == '\\' && at2 + 1 < json.length()) {
                out.append(json.charAt(++at2));
                continue;
            }
            if (c == '"') {
                break;
            }
            out.append(c);
        }
        return out.toString();
    }

    private static String escape(String value) {
        return value.replace("\\", "\\\\").replace("\"", "\\\"");
    }

    private static String nz(String value, String fallback) {
        return value == null || value.isBlank() ? fallback : value;
    }

    private static String env(String name, String fallback) {
        String value = System.getenv(name);
        return value == null || value.isBlank() ? fallback : value;
    }

    // ---------------------------------------------------------------- //
    //  multipart/form-data вручную: в стандартной библиотеке его нет
    // ---------------------------------------------------------------- //
    static final class Multipart {
        private final String boundary = "vistest-" + Long.toHexString(System.nanoTime());
        private final ByteArrayOutputStream out = new ByteArrayOutputStream();

        Multipart field(String name, String value) {
            write("--" + boundary + "\r\nContent-Disposition: form-data; name=\""
                    + name + "\"\r\n\r\n" + value + "\r\n");
            return this;
        }

        Multipart file(String name, String filename, String type, byte[] data) {
            write("--" + boundary + "\r\nContent-Disposition: form-data; name=\""
                    + name + "\"; filename=\"" + filename + "\"\r\nContent-Type: "
                    + type + "\r\n\r\n");
            out.write(data, 0, data.length);
            write("\r\n");
            return this;
        }

        String contentType() {
            return "multipart/form-data; boundary=" + boundary;
        }

        byte[] body() {
            write("--" + boundary + "--\r\n");
            return out.toByteArray();
        }

        private void write(String text) {
            byte[] bytes = text.getBytes(StandardCharsets.UTF_8);
            out.write(bytes, 0, bytes.length);
        }
    }

    // ---------------------------------------------------------------- //
    public static void main(String[] args) throws Exception {
        if (args.length < 4) {
            System.err.println(
                    "usage: java VisTest.java <api-url> <project> <name> <file.png>\n"
                  + "       token is taken from VISTEST_TOKEN, run key from VISTEST_RUN_ID");
            System.exit(2);
        }
        VisTest vt = new VisTest(args[0])
                .project(args[1])
                .token(System.getenv("VISTEST_TOKEN"))
                .runKey(System.getenv("VISTEST_RUN_ID"));
        Result r = vt.check(args[2], Path.of(args[3]));
        System.out.println(r.message());
        if (!vt.runKey.isEmpty()) {
            vt.finish();
        }
        System.exit(r.passed ? 0 : 1);
    }
}
